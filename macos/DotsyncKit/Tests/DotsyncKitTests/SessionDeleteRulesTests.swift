import Foundation
import Testing
@testable import DotsyncKit

private func deleteResult(_ id: String, _ outcome: String, freed: Int64? = nil) -> DeleteResult {
    DeleteResult(
        id: id, outcome: outcome, reason: nil, kept: nil, failed: nil, freedBytes: freed, children: nil,
        remaining: nil, unchecked: nil, message: nil)
}

private func report(_ results: [DeleteResult], stopped: Bool = false, tool: SessionTool = .codex) -> DeleteReport {
    DeleteReport(tool: tool, results: results, stopped: stopped)
}

private func logEntry(_ command: String, result: String) throws -> SessionLogEntry {
    let json = """
    {"command": "\(command)", "tool": "codex", "started_at": "2026-10-25T01:00:00Z",
     "finished_at": "2026-10-25T01:00:05Z", \(result)}
    """
    return try UsageJSON.decoder().decode(SessionLogEntry.self, from: Data(json.utf8))
}

@Test func confirmCountsTakeIdleChildrenAndBytes() {
    var parent = makeItem("a", bytes: 300)
    parent.children = 3
    var other = makeItem("b", bytes: 200)
    other.children = 1
    var running = makeItem("c", state: "running", bytes: 999)
    running.children = 7
    let list = makeList([makeGroup("g", gb: 1, items: [parent, other, running, makeItem("d", bytes: 50)])])
    // "c" is running now, "gone" is not in the list: neither counts.
    let counts = SessionDeleteRules.confirmCounts(["a", "b", "c", "gone"], in: list)
    #expect(counts == SessionDeleteRules.ConfirmCounts(count: 2, children: 4, bytes: 500))
    #expect(SessionDeleteRules.confirmCounts([], in: list) == SessionDeleteRules.ConfirmCounts(count: 0, children: 0, bytes: 0))
}

@Test func freedBytesSumsWhatTheCLIReported() {
    let sum = SessionDeleteRules.freedBytes(report([
        deleteResult("a", "deleted", freed: 300_000_000), deleteResult("b", "partial", freed: 100_000_000),
        deleteResult("c", "skipped"),
    ]))
    #expect(sum == 400_000_000)
    #expect(SessionDeleteRules.freedBytes(report([deleteResult("c", "skipped")])) == 0)
    #expect(SessionDeleteRules.freedBytes(report([])) == 0)
}

@Test func rowTitleFallsBackToTheIdsFirstEightCharacters() {
    var item = makeItem("12345678-aaaa-bbbb-cccc-1234567890ab")
    item.title = "마이그레이션 충돌 정리"
    let list = makeList([makeGroup("g", gb: 1, items: [item])])
    #expect(SessionDeleteRules.rowTitle(item.id, in: list) == "마이그레이션 충돌 정리")
    #expect(SessionDeleteRules.rowTitle("abcdef01-aaaa-bbbb-cccc-1234567890ab", in: list) == "abcdef01")
    #expect(SessionDeleteRules.rowTitle("abcdef01-aaaa-bbbb-cccc-1234567890ab", in: nil) == "abcdef01")
    #expect(SessionDeleteRules.rowTitle("abc", in: nil) == "abc")
}

@Test func onlyPartialRowsCanBeTriedAgain() {
    for outcome in ["deleted", "skipped", "restored", "failed"] {
        #expect(!SessionDeleteRules.canRetry(deleteResult("a", outcome)), "\(outcome)")
    }
    #expect(SessionDeleteRules.canRetry(deleteResult("a", "partial")))
}

@Test func aRetryReplacesOnlyItsOwnRow() {
    let before = report([deleteResult("a", "deleted", freed: 5), deleteResult("b", "partial", freed: 1), deleteResult("c", "skipped")])
    let retry = report([deleteResult("b", "deleted", freed: 9)])
    let after = SessionDeleteRules.merged(before, replacing: retry)
    #expect(after.results.map(\.id) == ["a", "b", "c"])
    #expect(after.results.map(\.outcome) == ["deleted", "deleted", "skipped"])
    #expect(SessionDeleteRules.freedBytes(after) == 14)
    #expect(!after.stopped)
    #expect(SessionDeleteRules.merged(before, replacing: report([], stopped: true)).stopped)
}

@Test func pendingStampKeepsWholeSecondsRoundedDown() throws {
    let at = Date(timeIntervalSince1970: 1_800_000_000.9)
    let stamp = SessionDeleteRules.pendingStamp(at)
    let start = try #require(SessionDeleteRules.pendingStart(stamp))
    // The CLI logs whole seconds: a start time rounded up would miss its log.
    #expect(start <= at)
    #expect(at.timeIntervalSince(start) < 1)
    #expect(SessionDeleteRules.pendingStart(nil) == nil)
    #expect(SessionDeleteRules.pendingStart("yesterday") == nil)
}

@Test func aPendingResultShowsALoggedDeleteRecoverOrCleanup() throws {
    let done = #""result": {"tool": "codex", "results": [{"id": "a", "outcome": "deleted"}], "stopped": false}"#
    guard case .delete(let shown)? = SessionDeleteRules.pendingResult(try logEntry("delete", result: done)) else {
        Issue.record("a logged delete shows its report")
        return
    }
    #expect(shown.tool == .codex)
    #expect(shown.results.map(\.id) == ["a"])
    guard case .delete? = SessionDeleteRules.pendingResult(try logEntry("recover", result: done)) else {
        Issue.record("a logged recover shows its report")
        return
    }

    #expect(SessionDeleteRules.pendingResult(nil) == nil)
    // Busy or refused: the log holds an error, not a report.
    let refused = #""error": {"code": "busy", "message": "x"}"#
    #expect(SessionDeleteRules.pendingResult(try logEntry("delete", result: refused)) == nil)
    #expect(SessionDeleteRules.pendingResult(try logEntry("cleanup", result: refused)) == nil)
}

@Test func aPendingCleanupShowsAsTheCleanupSheetsSummary() throws {
    // Stopped by a logout: what it did, and that it stopped.
    let stopped = #"""
    "result": {"items": [{"id": "history_rows", "outcome": "done", "count": 3},
                         {"id": "queue", "outcome": "skipped", "count": 0, "reason": "stopped"}],
               "vacuum": null, "stopped": true}
    """#
    guard case .cleanup(let summary)? = SessionDeleteRules.pendingResult(try logEntry("cleanup", result: stopped)) else {
        Issue.record("a logged cleanup shows its summary")
        return
    }
    #expect(summary.title == "정리를 멈췄어요")
    #expect(summary.rows.map(\.label) == ["대화 저장소에 남은 내용", "큐 기록"])
    #expect(summary.rows.map(\.text) == ["3개 정리했어요", "멈춰서 하지 않았어요"])
    // ChatGPT is the launch alert's question, not the summary's.
    #expect(summary.notes.isEmpty)
    #expect(!summary.relaunchFailed)

    let clean = #""result": {"items": [], "vacuum": null, "stopped": false}"#
    guard case .cleanup(let finished)? = SessionDeleteRules.pendingResult(try logEntry("cleanup", result: clean)) else {
        Issue.record("a logged cleanup shows its summary")
        return
    }
    #expect(finished.title == "정리했어요")
}
