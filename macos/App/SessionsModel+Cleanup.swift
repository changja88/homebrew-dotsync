import DotsyncKit
import Foundation

/// The Codex cleanup sheet's state: what it has read, what the person picked,
/// and where the unit is.
struct CleanupState {
    enum Phase {
        case choosing
        case running
        case finished(CleanupRules.Summary)
    }

    var phase = Phase.choosing
    var blockers: CleanupBlockers?
    var preview: CleanupPreview?
    /// The dry-run's progress while it reads.
    var readProgress: (done: Int, total: Int)?
    /// Why blockers or the dry-run could not be read.
    var failure: String?
    var selection: Set<String> = []
    /// The person's tick of the vacuum box, which starts on. What the box shows
    /// (and whether the vacuum runs) is `CleanupRules.vacuumBox`.
    var vacuum = true
    /// The open-session count the sheet last showed: "끄고 정리하기" confirms it.
    var shownOpenThreads = 0
    /// Opened from a delete's result: the list is read again on close.
    var reloadsListOnClose = false
}

extension SessionsModel {
    // MARK: Opening

    /// 정리… on the 세션 menu, and 남은 데이터 정리… on a delete's result. It
    /// reads the blockers (fast), then the dry-run, and shows each as it comes.
    func openCleanup() {
        guard service != nil, tools[.codex]?.work == nil, cleanupTask == nil else { return }
        var opened = CleanupState()
        if case .result? = tools[.codex]?.sheet { opened.reloadsListOnClose = true }
        cleanup = opened
        tools[.codex]?.sheet = .cleanup
        readCleanup(everything: true)
    }

    /// 다시 확인: the blockers again. The dry-run is read again only when it
    /// has not arrived or was refused; the person's picks stay otherwise.
    func recheckCleanup() {
        guard cleanupTask == nil else { return }
        readCleanup(everything: cleanup.preview == nil || cleanup.preview?.refusal != nil)
    }

    private func readCleanup(everything: Bool) {
        guard let service else { return }
        cleanupReadTask?.cancel()
        cleanup.failure = nil
        if everything {
            cleanup.preview = nil
            cleanup.readProgress = nil
        }
        let progress: SessionService.Progress = { [weak self] done, total, _ in
            Task { @MainActor in self?.cleanup.readProgress = (done, max(total, 1)) }
        }
        cleanupReadTask = Task {
            do {
                let seen = try await service.blockers()
                guard !Task.isCancelled else { return }
                accept(blockers: seen)
                guard everything else { return }
                let preview = try await service.cleanupPreview(progress: progress)
                guard !Task.isCancelled else { return }
                accept(preview: preview)
            } catch {
                guard !Task.isCancelled else { return }
                cleanup.failure = (error as? CLIError).map(SessionText.cleanupRefusal) ?? SettingsModel.describe(error)
            }
        }
    }

    private func accept(blockers seen: CleanupBlockers) {
        cleanup.blockers = seen
        cleanup.shownOpenThreads = seen.chatgptOpenThreads
    }

    /// The dry-run's own blocker fields are newer than the first read.
    private func accept(preview: CleanupPreview) {
        cleanup.preview = preview
        accept(blockers: CleanupBlockers(preview))
        cleanup.selection = CleanupRules.initialSelection(preview.items)
        cleanup.vacuum = true
    }

    func toggleCleanupItem(_ id: String) {
        if cleanup.selection.remove(id) == nil { cleanup.selection.insert(id) }
    }

    /// 취소, 확인, or Esc: close the sheet. A finished unit reads the list
    /// again, since the store changed.
    func closeCleanup() {
        guard cleanupTask == nil else { return }
        cleanupReadTask?.cancel()
        cleanupReadTask = nil
        var reload = cleanup.reloadsListOnClose
        if case .finished = cleanup.phase { reload = true }
        cleanup = CleanupState()
        tools[.codex]?.sheet = nil
        if reload { Task { await refresh(.codex) } }
    }

    // MARK: The unit

    /// 끄고 정리하기. The whole unit is one job in the Codex queue, so no
    /// delete starts in the middle of it. A second press while it runs does nothing.
    func startCleanup() async {
        guard let service, let queue = queues[.codex], tools[.codex]?.work == nil, cleanupTask == nil,
              case .choosing = cleanup.phase, let blockers = cleanup.blockers, let preview = cleanup.preview else { return }
        let items = CleanupRules.selectedIds(preview.items, selection: cleanup.selection)
        let vacuum = CleanupRules.reclaims(preview: preview, selection: cleanup.selection, ticked: cleanup.vacuum)
        guard CleanupRules.startState(blockers: blockers, preview: preview, selection: cleanup.selection,
                                      vacuum: cleanup.vacuum).allowed else { return }
        cleanupReadTask?.cancel()
        cleanupReadTask = nil
        cleanup.phase = .running
        let label = "Codex 정리 중…"
        // Before the unit's own CLI runs: a cleanup cut off by a logout or
        // crash shows its logged result on the next launch.
        holdPendingResult()
        tools[.codex]?.work = SessionWork(label: label, stoppable: true)
        let shown = cleanup.shownOpenThreads

        let progress: SessionService.Progress = { [weak self] done, total, phase in
            Task { @MainActor in self?.advance(.codex, done: done, total: total, phase: phase) }
        }
        let poweringOff: () -> Bool = { [weak self] in self?.isPoweringOff ?? false }
        var outcome: CleanupOutcome?
        await queue.run(label) {
            // 멈추기 pressed while this waited: nothing was attempted.
            guard !stopRequested.contains(.codex) else { return }
            let unit = CleanupUnit(control: chatGPT, runner: service, flags: flags)
            let task = Task {
                await unit.run(items: items, vacuum: vacuum, shownOpenThreads: shown, progress: progress,
                               isPoweringOff: poweringOff)
            }
            cleanupTask = task
            outcome = await task.value
        }
        // Every way out.
        cleanupTask = nil
        stopRequested.remove(.codex)
        tools[.codex]?.work = nil
        await finishCleanup(outcome)
        releasePendingResult()
    }

    private func finishCleanup(_ outcome: CleanupOutcome?) async {
        guard let outcome else {
            cleanup.phase = .choosing
            return
        }
        if let summary = CleanupRules.summary(of: outcome) {
            cleanup.phase = .finished(summary)
            return
        }
        // Nothing was touched: back to the sheet, with the new facts. A grown
        // count waits for another press.
        cleanup.phase = .choosing
        if case .needsConfirmation(let threads) = outcome {
            cleanup.shownOpenThreads = threads
            cleanup.blockers?.chatgptOpenThreads = threads
        } else {
            recheckCleanup()
        }
    }

    // MARK: ChatGPT

    /// 다시 켜기 on the launch alert, and "ChatGPT 다시 켜기" on the result.
    func relaunchChatGPT() async -> Bool {
        guard await chatGPT.relaunch() else { return false }
        flags.set(false, forKey: CleanupUnit.flagKey)
        if case .finished(let summary) = cleanup.phase { cleanup.phase = .finished(summary.afterRelaunch()) }
        return true
    }

    /// On launch: dotsync quit ChatGPT, and it is still off.
    var shouldOfferRelaunch: Bool {
        CleanupUnit.shouldOfferRelaunch(flags: flags, control: chatGPT)
    }

    /// 그대로 두기.
    func declineRelaunch() {
        flags.set(false, forKey: CleanupUnit.flagKey)
    }
}
