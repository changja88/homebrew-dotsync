import DotsyncKit
import Foundation
import Observation

/// What a delete (or recover) run leaves on screen. `cleanup` is the Codex
/// cleanup sheet, whose state is `SessionsModel.cleanup`.
enum SessionSheet {
    case confirm
    /// A delete or recover in flight; its progress is the tool's `work`.
    case running
    case result(DeleteReport)
    /// A delete or recover that threw (busy, CLI error, timeout).
    case failure(String)
    case cleanup
}

/// A delete, recover or cleanup run in flight, for quitting and the 설정 rows.
struct SessionWork: Equatable {
    var label: String
    var progress = RunProgress()
    /// 멈추기 is offered; undoing a delete (되돌리기) has no safe point to stop at.
    var stoppable = true
}

/// One tool's list on the 세션 menu, and what the person did to it.
struct ToolSessions {
    var list: SessionList?
    /// From asking for a list to its answer, the wait in the queue included.
    var checking = false
    var message: String?
    var selection: Set<String> = []
    /// Row ids (group keys, or "merged") that are open, and the ones showing
    /// every session instead of the first five.
    var expanded: Set<String> = []
    var showAll: Set<String> = []
    var sheet: SessionSheet?
    var work: SessionWork?
}

/// The 세션 menu's state. Each tool has its own queue, so two commands for the
/// same tool never overlap while Claude Code and Codex may run together.
@MainActor
@Observable
final class SessionsModel {
    var tools: [SessionTool: ToolSessions] = [.claude: ToolSessions(), .codex: ToolSessions()]
    let queues: [SessionTool: CommandQueue]
    let service: SessionService?
    /// Holds the start time of a delete, recover or cleanup from before it
    /// runs until its result is on screen, so a result the app missed
    /// (logout, crash) can be shown.
    static let pendingKey = "pendingSessionsResult"
    private let defaults: UserDefaults
    @ObservationIgnored private var runningTasks: [SessionTool: Task<DeleteReport, Error>] = [:]
    @ObservationIgnored var stopRequested: Set<SessionTool> = []
    /// The app quits as soon as the work ends (Task 22): a result that lands
    /// meanwhile goes unseen, so its start time stays for the next launch.
    @ObservationIgnored private var quitsWhenDone = false

    // The Codex cleanup (SessionsModel+Cleanup.swift).
    var cleanup = CleanupState()
    @ObservationIgnored private var powerOffStarted: ContinuousClock.Instant?
    /// From `powerOff(at:)` on, for as long as the app's power-off holds (60 s
    /// if the logout is cancelled): the unit starts no cleanup and relaunches
    /// nothing.
    var isPoweringOff: Bool { QuitRules.stillPoweringOff(since: powerOffStarted, now: .now) }
    let chatGPT: any ChatGPTControl
    let flags: any FlagStore
    @ObservationIgnored var cleanupTask: Task<CleanupOutcome, Never>?
    @ObservationIgnored var cleanupReadTask: Task<Void, Never>?

    init(claudeQueue: CommandQueue, codexQueue: CommandQueue, service: SessionService? = SessionService.standard(),
         defaults: UserDefaults = .standard, logDirectory: URL = SessionsModel.standardLogDirectory,
         chatGPT: (any ChatGPTControl)? = nil, flags: any FlagStore = UserDefaults.standard) {
        queues = [.claude: claudeQueue, .codex: codexQueue]
        self.service = service
        self.defaults = defaults
        self.chatGPT = chatGPT ?? ChatGPTApp()
        self.flags = flags
        if service == nil {
            let message = "dotsync를 찾을 수 없어요 — brew install changja88/dotsync/dotsync"
            for tool in SessionTool.allCases { tools[tool]?.message = message }
        }
        showPendingResult(logDirectory)
    }

    nonisolated static var standardLogDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/dotsync")
    }

    /// A delete or cleanup that began before the app closed: show what its log
    /// says, on the 세션 menu. With no log (it never ran, or left none) show
    /// nothing.
    private func showPendingResult(_ logDirectory: URL) {
        guard let stamp = defaults.string(forKey: Self.pendingKey) else { return }
        defaults.removeObject(forKey: Self.pendingKey)
        guard let since = SessionDeleteRules.pendingStart(stamp),
              let pending = SessionDeleteRules.pendingResult(SessionLog.latest(since: since, in: logDirectory)) else { return }
        let tool: SessionTool
        switch pending {
        case .delete(let report):
            tool = report.tool
            showResult(report)
        case .cleanup(let summary):
            tool = .codex
            cleanup.phase = .finished(summary)
            tools[tool]?.sheet = .cleanup
        }
        defaults.set(RailItem.sessions.rawValue, forKey: "rail")
        defaults.set(tool.rawValue, forKey: "sessionsTab")
    }

    /// Before a delete, recover or cleanup is queued. Another tool's run in
    /// flight keeps its earlier time.
    func holdPendingResult() {
        if !isWorking { defaults.set(SessionDeleteRules.pendingStamp(.now), forKey: Self.pendingKey) }
    }

    /// Once a run's result is on screen. The time stays while another tool's
    /// run goes on, and while the app is about to quit unseen.
    func releasePendingResult() {
        if !isWorking && !quitsWhenDone { defaults.removeObject(forKey: Self.pendingKey) }
    }

    /// Quitting waits for the work to end, or no longer does (the person
    /// closed the line and stays).
    func setQuitsWhenDone(_ quits: Bool) {
        quitsWhenDone = quits
        if !quits { releasePendingResult() }
    }

    /// The Mac is logging out or restarting: the cleanup unit starts no
    /// cleanup, stops one not yet registered as a long run, and relaunches
    /// nothing. The app stops the long runs themselves. `started` is the
    /// app's own power-off start, so both lapse together.
    func powerOff(at started: ContinuousClock.Instant) {
        powerOffStarted = started
        cleanupTask?.cancel()
    }

    subscript(tool: SessionTool) -> ToolSessions {
        tools[tool] ?? ToolSessions()
    }

    /// A delete, recover or cleanup is running, for quitting to wait for.
    var isWorking: Bool { tools.values.contains { $0.work != nil } }

    /// The runs in flight, Claude Code first, for the line quitting shows.
    var runningWork: [SessionWork] { SessionTool.allCases.compactMap { tools[$0]?.work } }

    func dismissMessage(_ tool: SessionTool) {
        tools[tool]?.message = nil
    }

    /// A list takes seconds to minutes, so it runs when a tab is first shown
    /// and on 다시 계산, never on a timer.
    func checkFirstTime(_ tool: SessionTool) async {
        guard self[tool].list == nil, !self[tool].checking else { return }
        await refresh(tool)
    }

    /// A failed list keeps the one on screen and says why; the selection is
    /// pruned only against a list that arrived.
    func refresh(_ tool: SessionTool) async {
        guard let service, let queue = queues[tool], !self[tool].checking else { return }
        tools[tool]?.checking = true
        await queue.run("확인 중…") {
            do {
                accept(try await service.list(tool), for: tool)
            } catch {
                tools[tool]?.message = SettingsModel.describe(error)
            }
        }
        tools[tool]?.checking = false
    }

    private func accept(_ list: SessionList, for tool: SessionTool) {
        guard var state = tools[tool] else { return }
        if state.list == nil, let first = SessionRules.initiallyExpanded(SessionRules.rows(list.groups)) {
            state.expanded = [first]
        }
        state.list = list
        state.selection = SessionRules.pruned(state.selection, in: list)
        state.message = nil
        tools[tool] = state
    }

    /// Letting go of a pick always works; picking needs an idle session of the
    /// current list.
    func toggle(_ id: String, in tool: SessionTool) {
        if tools[tool]?.selection.remove(id) != nil { return }
        guard let list = tools[tool]?.list, let item = SessionRules.item(id, in: list),
              SessionRules.isSelectable(item) else { return }
        tools[tool]?.selection.insert(id)
    }

    /// `key` is a group's key, or "merged" for the row of the small ones.
    func toggleGroup(_ key: String, in tool: SessionTool) {
        guard let state = tools[tool], let list = state.list,
              let row = SessionRules.rows(list.groups).first(where: { $0.id == key }) else { return }
        tools[tool]?.selection = SessionRules.togglingGroup(state.selection, selectable: SessionRules.selectable(in: row))
    }

    func toggleExpanded(_ key: String, in tool: SessionTool) {
        if tools[tool]?.expanded.remove(key) == nil { tools[tool]?.expanded.insert(key) }
    }

    func showAll(_ key: String, in tool: SessionTool) {
        tools[tool]?.showAll.insert(key)
    }

    /// Replaces the selection with the idle sessions with no record for `days`.
    func quickSelect(days: Int, tool: SessionTool) {
        guard let list = tools[tool]?.list else { return }
        tools[tool]?.selection = SessionRules.quickSelect(list, olderThanDays: days, now: .now)
    }

    func clearSelection(_ tool: SessionTool) {
        tools[tool]?.selection = []
    }

    // MARK: Delete and recover

    /// 지우기 in the confirm sheet. Only ids that are still in the latest list
    /// and idle go to the CLI; with none left nothing runs.
    func confirmDelete(_ tool: SessionTool) async {
        guard let state = tools[tool], let list = state.list, state.work == nil else { return }
        let ids = SessionRules.pruned(state.selection, in: list).sorted()
        guard !ids.isEmpty else {
            tools[tool]?.sheet = nil
            return
        }
        await runDelete(tool, ids: ids, label: "지우는 중…", previous: nil)
    }

    /// 다시 시도 on a partial row: only that id runs again, and its new result
    /// takes the row's place.
    func retry(_ id: String, in report: DeleteReport) async {
        guard tools[report.tool]?.work == nil else { return }
        await runDelete(report.tool, ids: [id], label: "다시 지우는 중…", previous: report)
    }

    /// 되돌리기 (`finish: false`) or 마저 지우기 on a Claude session that a
    /// delete left half done.
    func recover(_ id: String, finish: Bool) async {
        guard let service, tools[.claude]?.work == nil else { return }
        await run(.claude, label: finish ? "마저 지우는 중…" : "되돌리는 중…", total: 1, stoppable: finish, previous: nil) {
            try await service.recover(id, finish: finish, progress: $0)
        }
    }

    private func runDelete(_ tool: SessionTool, ids: [String], label: String, previous: DeleteReport?) async {
        guard let service else { return }
        await run(tool, label: label, total: ids.count, previous: previous) {
            try await service.delete(tool, ids: ids, progress: $0)
        }
    }

    /// Runs through the tool's queue. The task is made once the queue's turn
    /// comes; 멈추기 pressed before that cancels it at its start. The CLI then
    /// answers with what it did, and that answer is what the sheet shows.
    private func run(_ tool: SessionTool, label: String, total: Int, stoppable: Bool = true, previous: DeleteReport?,
                     operation: @escaping @Sendable (@escaping SessionService.Progress) async throws -> DeleteReport) async {
        guard let queue = queues[tool], tools[tool]?.work == nil else { return }
        holdPendingResult()
        tools[tool]?.work = SessionWork(label: label, progress: RunProgress(done: 0, total: total), stoppable: stoppable)
        tools[tool]?.sheet = .running
        await queue.run(label) {
            let progress: SessionService.Progress = { [weak self] done, total, phase in
                Task { @MainActor in self?.advance(tool, done: done, total: total, phase: phase) }
            }
            // 멈추기 pressed while this waited: nothing was attempted, so the
            // selection stays and no result is shown.
            if stopRequested.contains(tool) {
                tools[tool]?.sheet = nil
                return
            }
            let task = Task { try await operation(progress) }
            runningTasks[tool] = task
            do {
                let report = try await task.value
                showResult(previous.map { SessionDeleteRules.merged($0, replacing: report) } ?? report)
            } catch {
                tools[tool]?.sheet = .failure(SessionText.failureLine(error))
            }
        }
        // Every way out: the result or the failure is on screen now.
        runningTasks[tool] = nil
        stopRequested.remove(tool)
        tools[tool]?.work = nil
        releasePendingResult()
    }

    func advance(_ tool: SessionTool, done: Int, total: Int, phase: String?) {
        tools[tool]?.work?.progress.advance(done: done, total: total, phase: phase)
    }

    /// The result sheet, and at once the list without what it deleted, so the
    /// rows are gone, unchecked, and the totals right while the re-read on 확인
    /// takes its time.
    private func showResult(_ report: DeleteReport) {
        tools[report.tool]?.sheet = .result(report)
        guard let list = tools[report.tool]?.list else { return }
        tools[report.tool]?.list = SessionRules.applying(report, to: list)
        tools[report.tool]?.selection.subtract(report.results.filter { $0.outcome == "deleted" }.map(\.id))
    }

    /// 멈추기: the CLI stops at its next safe point and reports.
    func stop(_ tool: SessionTool) {
        stopRequested.insert(tool)
        runningTasks[tool]?.cancel()
        if tool == .codex { cleanupTask?.cancel() }
    }

    /// 확인 on a result: close it and read the list again, whose pruning drops
    /// the deleted ids from the selection.
    func dismissResult(_ tool: SessionTool) async {
        tools[tool]?.sheet = nil
        await refresh(tool)
    }

    func cancelConfirm(_ tool: SessionTool) {
        switch tools[tool]?.sheet {
        case .confirm?: tools[tool]?.sheet = nil
        case .cleanup? where cleanupTask == nil && tools[tool]?.work == nil: closeCleanup()
        default: break
        }
    }

    func askToDelete(_ tool: SessionTool) {
        guard self[tool].work == nil else { return }
        tools[tool]?.sheet = .confirm
    }
}
