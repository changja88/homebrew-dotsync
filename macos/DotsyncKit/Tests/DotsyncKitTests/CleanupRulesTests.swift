import Foundation
import Testing
@testable import DotsyncKit

private func proc(_ kind: String, _ name: String, pid: Int = 1, elapsed: Int = 720) -> Blocker {
    Blocker(pid: pid, kind: kind, name: name, elapsed: elapsed)
}

private func seen(_ blockers: [Blocker], threads: Int = 0) -> CleanupBlockers {
    CleanupBlockers(blockers: blockers, chatgptHelpers: [], chatgptOpenThreads: threads)
}

private func item(_ id: String, count: Int, lines: Int? = nil, checked: Bool = true) -> CleanupItem {
    CleanupItem(id: id, label: id, count: count, lines: lines, bytes: nil, checked: checked)
}

private func plan(items: [CleanupItem] = [], refusal: CLIError? = nil) -> CleanupPreview {
    CleanupPreview(blockers: [], chatgptHelpers: [], chatgptOpenThreads: 0, items: items,
                   historyDbBytes: 8_600_000_000, vacuumAvailable: true, refusal: refusal)
}

private func done(_ id: String, count: Int, lines: Int? = nil) -> CleanupItemResult {
    CleanupItemResult(id: id, outcome: "done", count: count, lines: lines, reason: nil, message: nil)
}

// MARK: Rows before the start

@Test func chatgptRowIsNeutralWithNoOpenSessionsAndWarnsWithSome() {
    let quiet = CleanupRules.startRows(seen([proc("chatgpt_app", "ChatGPT"), proc("chatgpt_child", "codex")]))
    #expect(quiet.count == 1)
    #expect(quiet[0].style == .ok)
    #expect(quiet[0].title == "ChatGPT 앱")
    #expect(quiet[0].detail == "dotsync가 끄고, 끝나면 다시 켜요")
    #expect(!quiet[0].canRecheck)

    let open = CleanupRules.startRows(seen([proc("chatgpt_app", "ChatGPT")], threads: 3))
    #expect(open[0].style == .warn)
    #expect(open[0].detail == "열린 세션 3개가 닫혀요. 하던 작업이 있으면 멈춰요")
}

@Test func otherBlockersGroupByKindAndNameWithTheLongestRun() {
    let rows = CleanupRules.startRows(seen([
        proc("chatgpt_app", "ChatGPT"),
        proc("terminal_codex", "codex", pid: 20, elapsed: 720),
        proc("terminal_codex", "codex", pid: 21, elapsed: 125),
        proc("terminal_codex", "codex-exec", pid: 22, elapsed: 60),
        proc("other", "node", pid: 23, elapsed: 3600),
    ]))
    let others = rows.filter { $0.style == .wait }
    #expect(others.map(\.text) == [
        "터미널 Codex 2개 · codex · 12분째 실행 중 · 끝나야 시작할 수 있어요",
        "터미널 Codex 1개 · codex-exec · 1분째 실행 중 · 끝나야 시작할 수 있어요",
        "Codex를 쓰는 프로그램 1개 · node · 60분째 실행 중 · 끝나야 시작할 수 있어요",
    ])
    #expect(others.allSatisfy { $0.canRecheck })
    #expect(rows.first?.title == "ChatGPT 앱")
}

@Test func aRunUnderAMinuteSaysSo() {
    let rows = CleanupRules.startRows(seen([proc("terminal_codex", "codex", elapsed: 20)]))
    #expect(rows[0].text == "터미널 Codex 1개 · codex · 1분 미만 실행 중 · 끝나야 시작할 수 있어요")
}

@Test func detectionFailureIsOneRowAndNothingRunningIsSaid() {
    let failed = CleanupRules.startRows(seen([proc("detection_failed", "lsof", elapsed: 0), proc("detection_failed", "ps", elapsed: 0)]))
    #expect(failed.count == 1)
    #expect(failed[0].detail == "실행 중인 Codex를 확인하지 못했어요")
    #expect(failed[0].style == .wait)
    #expect(failed[0].canRecheck)

    let none = CleanupRules.startRows(seen([]))
    #expect(none.count == 1)
    #expect(none[0].style == .ok)
    #expect(none[0].detail == "Codex를 쓰는 프로그램이 없어요")
}

// MARK: Items

@Test func theCacheItemIsNotARowAndRowsStartAtChecked() {
    let items = [item("history_rows", count: 4), item("queue", count: 0, checked: false), item("cache_files", count: 9)]
    #expect(CleanupRules.visibleItems(items).map(\.id) == ["history_rows", "queue"])
    #expect(CleanupRules.initialSelection(items) == ["history_rows"])
}

@Test func selectedIdsFollowThePreviewOrder() {
    let items = [item("history_rows", count: 4), item("history_lines", count: 2), item("queue", count: 1)]
    #expect(CleanupRules.selectedIds(items, selection: ["queue", "history_rows", "gone"]) == ["history_rows", "queue"])
}

@Test func countTextNamesSessionsAndLines() {
    #expect(CleanupRules.countText(item("history_rows", count: 2304)) == "세션 2,304개")
    #expect(CleanupRules.countText(item("history_lines", count: 2, lines: 37)) == "세션 2개 · 37줄")
    #expect(CleanupRules.countText(item("session_index", count: 83)) == "83개")
    #expect(CleanupRules.countText(item("queue", count: 2)) == "2개")
}

// MARK: The button

@Test func startNeedsAClearGateAndSomethingPicked() {
    let ready = plan(items: [item("queue", count: 1)])
    #expect(CleanupRules.startState(blockers: seen([]), preview: ready, selected: ["queue"], vacuum: false).allowed)
    let nothing = CleanupRules.startState(blockers: seen([]), preview: ready, selected: [], vacuum: false)
    #expect(!nothing.allowed)
    #expect(nothing.note == "정리할 것을 골라 주세요")
    // The vacuum alone is something to do.
    #expect(CleanupRules.startState(blockers: seen([]), preview: ready, selected: [], vacuum: true).allowed)
    let terminal = CleanupRules.startState(blockers: seen([proc("terminal_codex", "codex")]), preview: ready, selected: ["queue"], vacuum: false)
    #expect(!terminal.allowed)
    #expect(terminal.note == "터미널 Codex가 끝나면 이 버튼이 켜져요")
}

@Test func aRefusedDryRunShowsTheRefusalCopy() {
    let refused = plan(refusal: CLIError(code: "codex_version", message: "x"))
    let state = CleanupRules.startState(blockers: seen([]), preview: refused, selected: [], vacuum: false)
    #expect(!state.allowed)
    #expect(state.note == "지금 Codex 기록이 dotsync가 아는 형태가 아니라서 정리할 수 없어요")
}

@Test func vacuumLineNamesTheStoreSize() {
    #expect(CleanupRules.vacuumHint(historyDbBytes: 8_600_000_000) == "8.6 GB에서 지운 만큼 줄어요. 얼마나 줄지는 정리해 봐야 알아요.")
}

// MARK: Summary

@Test func finishedSummaryHidesTheCacheItemAndListsTheRest() {
    let report = CleanupReport(
        items: [done("history_rows", count: 4), done("history_lines", count: 2, lines: 37), done("cache_files", count: 9)],
        vacuum: VacuumResult(bytesBefore: 8_600_000_000, bytesAfter: 6_100_000_000, stoppedBecause: "freelist_empty", message: nil),
        stopped: false)
    let summary = CleanupRules.summary(of: .finished(report, relaunch: .relaunched))
    #expect(summary?.title == "정리했어요")
    #expect(summary?.rows.map(\.label) == ["대화 저장소에 남은 내용", "입력 기록에서 지운 세션의 줄"])
    #expect(summary?.rows.map(\.text) == ["4개 정리했어요", "2개 정리했어요 · 37줄"])
    #expect(summary?.vacuumLines == ["대화 저장소가 8.6 GB에서 6.1 GB로 줄었어요"])
    #expect(summary?.relaunchFailed == false)
}

@Test func summaryTitlesFollowWhatHappened() {
    let failed = CleanupItemResult(id: "queue", outcome: "failed", count: 0, lines: nil, reason: nil, message: "disk full")
    let partial = CleanupReport(items: [failed], vacuum: nil, stopped: false)
    #expect(CleanupRules.summary(of: .finished(partial, relaunch: .notNeeded))?.title == "일부는 정리하지 못했어요")
    #expect(CleanupRules.summary(of: .finished(partial, relaunch: .notNeeded))?.rows.map(\.text) == ["하지 못했어요 · disk full"])
    let stopped = CleanupReport(items: [], vacuum: nil, stopped: true)
    #expect(CleanupRules.summary(of: .finished(stopped, relaunch: .notNeeded))?.title == "정리를 멈췄어요")
}

@Test func relaunchFailureOffersTheButton() {
    let report = CleanupReport(items: [], vacuum: nil, stopped: false)
    let summary = CleanupRules.summary(of: .finished(report, relaunch: .failed))
    #expect(summary?.relaunchFailed == true)
    #expect(summary?.notes == ["ChatGPT를 다시 켜지 못했어요"])
    #expect(CleanupRules.summary(of: .finished(report, relaunch: .skippedForPowerOff))?.relaunchFailed == false)
}

@Test func aLaterRelaunchClearsTheFailure() {
    let report = CleanupReport(items: [], vacuum: nil, stopped: true)
    let failed = CleanupRules.summary(of: .finished(report, relaunch: .failed))
    let fixed = failed?.afterRelaunch()
    #expect(fixed?.relaunchFailed == false)
    #expect(fixed?.notes == [])
    #expect(fixed?.title == "정리를 멈췄어요")
}

@Test func chatgptThatDidNotQuitListsWhatIsLeft() {
    let summary = CleanupRules.summary(of: .chatgptDidNotQuit(["ChatGPT", "codex"], relaunch: .relaunched))
    #expect(summary?.title == "ChatGPT를 끄지 못했어요")
    #expect(summary?.notes == ["남아 있는 것: ChatGPT, codex", "아무것도 정리하지 않았어요"])
}

@Test func aFailedUnitSaysWhyInTheRefusalCopy() {
    let refusal = CLIError(code: "codex_running", message: "quit first")
    let summary = CleanupRules.summary(of: .failed(refusal, relaunch: .failed))
    #expect(summary?.title == "정리하지 못했어요")
    #expect(summary?.notes == ["Codex가 실행 중이라 정리하지 않았어요", "ChatGPT를 다시 켜지 못했어요"])
    let cancelled = CleanupRules.summary(of: .failed(CLIError(code: "cancelled", message: "dotsync was stopped"), relaunch: .notNeeded))
    #expect(cancelled?.notes == ["멈춰서 정리하지 않았어요"])
}

@Test func confirmationAndBlockedOutcomesHaveNoSummary() {
    #expect(CleanupRules.summary(of: .needsConfirmation(openThreads: 2)) == nil)
    #expect(CleanupRules.summary(of: .blocked([proc("terminal_codex", "codex")])) == nil)
}

// MARK: Count-0 items

@Test func emptyItemsAreNeverSelected() {
    let items = [item("history_rows", count: 4), item("queue", count: 0)]
    #expect(CleanupRules.selectedIds(items, selection: ["history_rows", "queue"]) == ["history_rows"])
    let onlyEmpty = plan(items: [item("queue", count: 0, checked: true)])
    let state = CleanupRules.startState(blockers: seen([]), preview: onlyEmpty,
                                        selected: CleanupRules.selectedIds(onlyEmpty.items, selection: ["queue"]), vacuum: false)
    #expect(!state.allowed)
    #expect(state.note == "정리할 것을 골라 주세요")
    #expect(CleanupRules.initialSelection(onlyEmpty.items).isEmpty)
}
