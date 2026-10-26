import Foundation

/// What the Codex cleanup sheet shows and when it lets the person start
/// (spec "Codex cleanup sheet"): the rows before the start, the items, the
/// button's state and the summary of a finished unit. The view only lays it out.
public enum CleanupRules {
    // MARK: 시작하기 전에

    public struct StartRow: Equatable, Sendable, Identifiable {
        public enum Style: Sendable { case ok, warn, wait }
        public var id: String
        public var style: Style
        public var title: String
        public var detail: String
        /// "다시 확인" is offered on the rows that wait for something.
        public var canRecheck: Bool

        /// The row as one line: "터미널 Codex 1개 · codex · 12분째 실행 중 · …".
        public var text: String { title.isEmpty ? detail : "\(title) · \(detail)" }
    }

    /// The ChatGPT row, then one row per kind and name of the other blockers.
    /// With nothing running, one neutral row says so.
    public static func startRows(_ seen: CleanupBlockers) -> [StartRow] {
        var rows: [StartRow] = []
        if seen.blockers.contains(where: \.isChatGPT) {
            let open = seen.chatgptOpenThreads
            rows.append(StartRow(
                id: "chatgpt", style: open > 0 ? .warn : .ok, title: "ChatGPT 앱",
                detail: open > 0 ? "열린 세션 \(open)개가 닫혀요. 하던 작업이 있으면 멈춰요" : "dotsync가 끄고, 끝나면 다시 켜요",
                canRecheck: false))
        }
        let others = seen.blockers.filter { !$0.isChatGPT }
        if others.contains(where: { $0.kind == "detection_failed" }) {
            rows.append(StartRow(id: "detection_failed", style: .wait, title: "Codex 확인",
                                 detail: "실행 중인 Codex를 확인하지 못했어요", canRecheck: true))
            return rows
        }
        rows += groups(others).map { group in
            StartRow(id: "\(group.kind)/\(group.name)", style: .wait,
                     title: "\(kindLabel(group.kind)) \(group.count)개",
                     detail: "\(group.name) · \(running(group.elapsed)) · 끝나야 시작할 수 있어요", canRecheck: true)
        }
        if rows.isEmpty {
            rows.append(StartRow(id: "none", style: .ok, title: "Codex", detail: "Codex를 쓰는 프로그램이 없어요", canRecheck: false))
        }
        return rows
    }

    private struct Group {
        var kind: String
        var name: String
        var count = 0
        var elapsed = 0
    }

    /// By kind and name, in the order first seen; the longest run of a group.
    private static func groups(_ blockers: [Blocker]) -> [Group] {
        var groups: [Group] = []
        for blocker in blockers {
            if let index = groups.firstIndex(where: { $0.kind == blocker.kind && $0.name == blocker.name }) {
                groups[index].count += 1
                groups[index].elapsed = max(groups[index].elapsed, blocker.elapsed)
            } else {
                groups.append(Group(kind: blocker.kind, name: blocker.name, count: 1, elapsed: blocker.elapsed))
            }
        }
        return groups
    }

    private static func kindLabel(_ kind: String) -> String {
        kind == "terminal_codex" ? "터미널 Codex" : "Codex를 쓰는 프로그램"
    }

    private static func running(_ elapsed: Int) -> String {
        elapsed < 60 ? "1분 미만 실행 중" : "\(elapsed / 60)분째 실행 중"
    }

    // MARK: 정리할 것

    /// The dry-run's items, without `cache_files`: it always runs, last, and
    /// is not a choice (spec 6.5).
    public static func visibleItems(_ items: [CleanupItem]) -> [CleanupItem] {
        items.filter { $0.id != "cache_files" }
    }

    /// Each checkbox starts at the item's `checked`. An item with nothing to
    /// clean is never picked: it would quit ChatGPT for nothing.
    public static func initialSelection(_ items: [CleanupItem]) -> Set<String> {
        Set(visibleItems(items).filter { $0.checked && $0.count > 0 }.map(\.id))
    }

    /// What goes to `--items`: the picked ones that are still in the dry-run, in its order.
    public static func selectedIds(_ items: [CleanupItem], selection: Set<String>) -> [String] {
        visibleItems(items).filter { $0.count > 0 }.map(\.id).filter(selection.contains)
    }

    /// The right column of an item: "세션 2,304개", "세션 2개 · 37줄", "83개".
    public static func countText(_ item: CleanupItem) -> String {
        let count = item.count.formatted(.number.locale(Locale(identifier: "en_US")))
        if let lines = item.lines { return "세션 \(count)개 · \(lines.formatted(.number.locale(Locale(identifier: "en_US"))))줄" }
        return item.id == "history_rows" ? "세션 \(count)개" : "\(count)개"
    }

    // MARK: 빈 공간 돌려받기

    /// The vacuum box under the items. It shows only when the CLI can vacuum.
    public struct VacuumBox: Equatable, Sendable {
        public var enabled: Bool
        /// What the person sees; the vacuum runs only when this is on.
        public var ticked: Bool
        public var hint: String
    }

    /// At least one picked item with something in it.
    private static func isCleaning(_ preview: CleanupPreview, _ selection: Set<String>) -> Bool {
        !selectedIds(preview.items, selection: selection).isEmpty
    }

    /// `ticked` is the person's own tick, which starts on. Free space makes
    /// the box usable; with none, only a cleaning that will make some does.
    /// With neither, it shows off and disabled, and the person's tick waits
    /// for the next pick.
    public static func vacuumBox(preview: CleanupPreview, selection: Set<String>, ticked: Bool) -> VacuumBox? {
        guard preview.vacuumAvailable else { return nil }
        let cleaning = isCleaning(preview, selection)
        let free = preview.vacuumFreeBytes ?? 0
        if free > 0 {
            let now = "지금 \(SessionText.bytes(free))를 돌려받을 수 있어요"
            return VacuumBox(enabled: true, ticked: ticked, hint: cleaning ? now + ". 정리한 만큼 더 줄어요." : now)
        }
        if cleaning {
            return VacuumBox(enabled: true, ticked: ticked, hint: "정리한 만큼 줄어요. 얼마나 줄지는 정리해 봐야 알아요.")
        }
        return VacuumBox(enabled: false, ticked: false, hint: "돌려받을 빈 공간이 없어요")
    }

    /// Whether the vacuum will run: the box shows ticked and enabled.
    public static func reclaims(preview: CleanupPreview, selection: Set<String>, ticked: Bool) -> Bool {
        guard let box = vacuumBox(preview: preview, selection: selection, ticked: ticked) else { return false }
        return box.enabled && box.ticked
    }

    // MARK: 끄고 정리하기

    /// The blocker gate, then something to clean: an item, or the vacuum
    /// alone when the dry-run found free space for it. With neither, the
    /// unit would quit ChatGPT for nothing. `vacuum` is the person's tick.
    public static func startState(blockers: CleanupBlockers, preview: CleanupPreview?, selection: Set<String>,
                                  vacuum: Bool) -> (allowed: Bool, note: String?) {
        let gate = CleanupGate.canStart(blockers: blockers, preview: preview)
        guard gate.allowed, let preview else { return gate }
        if isCleaning(preview, selection) || reclaims(preview: preview, selection: selection, ticked: vacuum) { return (true, nil) }
        let somethingToClean = visibleItems(preview.items).contains { $0.count > 0 }
        let somethingToReclaim = preview.vacuumAvailable && (preview.vacuumFreeBytes ?? 0) > 0
        return (false, somethingToClean || somethingToReclaim ? "정리할 것을 골라 주세요" : "지금은 정리할 게 없어요")
    }

    // MARK: After the unit

    public struct ResultRow: Equatable, Sendable {
        /// ✓ for an item that ran, ! for one that failed, and a mark of its
        /// own for one that was skipped (stopped, or a Codex started).
        public enum Mark: Sendable { case done, skipped, failed }
        public var label: String
        public var text: String
        public var mark: Mark
    }

    /// The heading over the result rows. It names no outcome: skipped and
    /// failed items are listed too, each with its own mark.
    public static let resultsHeading = "항목별 결과"

    public struct Summary: Equatable, Sendable {
        public var title: String
        public var rows: [ResultRow] = []
        public var vacuumLines: [String] = []
        /// Lines under the title: what was left, or why nothing ran.
        public var notes: [String] = []
        /// ChatGPT did not open again: the sheet offers "ChatGPT 다시 켜기".
        public var relaunchFailed = false

        /// After "ChatGPT 다시 켜기" opened it: the failure line goes.
        public func afterRelaunch() -> Summary {
            var summary = self
            summary.relaunchFailed = false
            summary.notes.removeAll { $0 == CleanupRules.relaunchFailedNote }
            return summary
        }
    }

    static let relaunchFailedNote = "ChatGPT를 다시 켜지 못했어요"

    /// nil for the outcomes that end in the sheet as it was: a grown count
    /// (wait for another press) and a new blocker (the rows show it).
    public static func summary(of outcome: CleanupOutcome) -> Summary? {
        switch outcome {
        case .needsConfirmation, .blocked:
            return nil
        case .chatgptDidNotQuit(let names, let relaunch):
            return finish(Summary(title: "ChatGPT를 끄지 못했어요",
                                  notes: ["남아 있는 것: \(names.joined(separator: ", "))", "아무것도 정리하지 않았어요"]), relaunch)
        case .failed(let error, let relaunch):
            let why = error.code == "cancelled" ? "멈춰서 정리하지 않았어요" : SessionText.cleanupRefusal(error)
            return finish(Summary(title: "정리하지 못했어요", notes: [why]), relaunch)
        case .finished(let report, let relaunch):
            let rows = report.items.filter { $0.id != "cache_files" }.map {
                ResultRow(label: SessionText.cleanupItemLabels[$0.id] ?? $0.id, text: SessionText.cleanupResultLine($0),
                          mark: mark($0))
            }
            return finish(Summary(title: title(report), rows: rows,
                                  vacuumLines: report.vacuum.map(SessionText.vacuumLines) ?? []), relaunch)
        }
    }

    /// From the outcomes, items and vacuum alike: a stop (SIGTERM) and a
    /// Codex that started are told apart, then a failure; "정리했어요" needs
    /// an item that removed something (the cache files count) or a vacuum
    /// that freed space.
    private static func title(_ report: CleanupReport) -> String {
        let vacuum = report.vacuum?.stoppedBecause
        if report.stopped || vacuum == "stopped" { return "정리를 멈췄어요" }
        if report.items.contains(where: { $0.reason == "codex_started" }) || vacuum == "blocker" {
            return "Codex가 켜져서 정리를 멈췄어요"
        }
        if report.items.contains(where: { $0.outcome == "failed" }) || vacuum == "error" { return "일부는 정리하지 못했어요" }
        let removed = report.items.contains { $0.outcome == "done" && $0.count > 0 }
        let freed = report.vacuum.map { $0.bytesAfter < $0.bytesBefore } ?? false
        return removed || freed ? "정리했어요" : "정리할 게 없었어요"
    }

    /// An outcome the app does not know is never shown as done.
    private static func mark(_ item: CleanupItemResult) -> ResultRow.Mark {
        switch item.outcome {
        case "done": return .done
        case "failed": return .failed
        default: return .skipped
        }
    }

    private static func finish(_ summary: Summary, _ relaunch: RelaunchResult) -> Summary {
        var summary = summary
        if relaunch == .failed {
            summary.notes.append(relaunchFailedNote)
            summary.relaunchFailed = true
        }
        return summary
    }
}

extension CleanupBlockers {
    /// The blocker fields of a dry-run: newer than an earlier `--blockers`.
    public init(_ preview: CleanupPreview) {
        self.init(blockers: preview.blockers, chatgptHelpers: preview.chatgptHelpers,
                  chatgptOpenThreads: preview.chatgptOpenThreads)
    }
}
