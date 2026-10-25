import Foundation
import Testing
@testable import DotsyncKit

// The fixtures are the CLI's own answers, made by scripts/make-session-fixtures.py
// from the Python tests' fake homes.

func fixture(_ name: String) throws -> Data {
    let url = try #require(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
    return try Data(contentsOf: url)
}

func decodeSession<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try UsageJSON.decoder().decode(type, from: Data(json.utf8))
}

@Test func sessionListDecodes() throws {
    let claude = try UsageJSON.decoder().decode(SessionList.self, from: fixture("claude-list"))
    #expect(claude.tool == .claude)
    #expect((claude.totals.sessions, claude.totals.running) == (3, 1))
    #expect(claude.totals.historyDbBytes == nil)
    #expect(claude.agentsError == nil)
    let items = claude.groups.flatMap(\.items)
    #expect(Dictionary(uniqueKeysWithValues: items.map { ($0.id, $0.state) }) == [
        "11111111-1111-4111-8111-111111111111": "idle",
        "22222222-2222-4222-8222-222222222222": "running",
        "33333333-3333-4333-8333-333333333333": "unknown",
    ])
    let lost = try #require(items.first { $0.state == "unknown" })
    #expect((lost.reason, lost.placeReason) == ("recent", "not_found"))
    #expect(items.first { $0.state == "running" }?.subagents == 1)
    #expect(claude.totals.bytes == items.reduce(0) { $0 + $1.bytes })
    let group = try #require(claude.groups.first { $0.name == "proj" })
    #expect(group.id == group.key)
    #expect((group.folders, group.foldersDeleted, group.sessions, group.running) == (2, 0, 2, 1))

    let codex = try UsageJSON.decoder().decode(SessionList.self, from: fixture("codex-list"))
    #expect(codex.tool == .codex)
    #expect(codex.totals.historyDbBytes ?? 0 > 0)
    let root = try #require(codex.groups.flatMap(\.items).first { $0.children == 2 })
    #expect((root.title, root.cwd, root.archived) == ("a named root", "/work/repo", false))
    #expect(codex.groups.flatMap(\.items).contains { $0.archived == true })
}

@Test func claudeDeleteReportDecodes() throws {
    let report = try UsageJSON.decoder().decode(DeleteReport.self, from: fixture("claude-delete-report"))
    #expect(report.tool == .claude)
    #expect(report.stopped == false)
    #expect(report.results.map(\.outcome) == ["deleted", "skipped", "partial", "skipped"])
    #expect(report.results[0].freedBytes ?? 0 > 0)
    let renameFailed = report.results[1]
    #expect(renameFailed.reason == "rename_failed")
    #expect(renameFailed.failed?.count == 1)
    #expect(renameFailed.failed?.first?.path.hasSuffix("22222222-2222-4222-8222-222222222222.jsonl") == true)
    let partial = report.results[2]
    #expect(partial.failed?.first?.error.contains("Permission denied") == true)
    #expect(report.results[3].reason == "running")
}

@Test func codexDeleteReportDecodes() throws {
    let report = try UsageJSON.decoder().decode(DeleteReport.self, from: fixture("codex-delete-report"))
    #expect(report.tool == .codex)
    #expect(report.results.map(\.outcome) == ["deleted", "skipped", "partial"])
    #expect(report.results[0].children == 2)
    #expect(report.results[1].reason == "refused")
    #expect(report.results[1].message == "Error: failed to delete session")
    #expect(report.results[2].remaining == ["history"])
    #expect(report.results[2].unchecked == ["history"])
}

@Test func deleteReportKeepsKeptAndToleratesUnknownValues() throws {
    let report = try decodeSession(DeleteReport.self, """
    {"tool": "claude", "stopped": true, "future_key": 1, "results": [
      {"id": "a", "outcome": "something_new", "reason": "also_new", "kept": ["tool_results"], "x": {"y": 1}}]}
    """)
    #expect(report.stopped)
    #expect(report.results[0].outcome == "something_new")
    #expect(report.results[0].kept == ["tool_results"])
    #expect(report.results[0].freedBytes == nil)
}

@Test func cleanupBlockersDecode() throws {
    let blockers = try decodeSession(CleanupBlockers.self, """
    {"blockers": [{"pid": 9, "kind": "terminal_codex", "name": "codex", "elapsed": 12},
                  {"pid": 0, "kind": "detection_failed", "name": "lsof", "elapsed": 0}],
     "chatgpt_helpers": [{"pid": 5, "kind": "chatgpt_helper", "name": "helper", "elapsed": 3}],
     "chatgpt_open_threads": 2}
    """)
    #expect(blockers.blockers.map(\.kind) == ["terminal_codex", "detection_failed"])
    #expect(blockers.blockers[0].elapsed == 12)
    #expect(blockers.chatgptHelpers.map(\.pid) == [5])
    #expect(blockers.chatgptOpenThreads == 2)
}

@Test func cleanupPreviewDecodes() throws {
    let preview = try UsageJSON.decoder().decode(CleanupPreview.self, from: fixture("cleanup-dry-run"))
    #expect(preview.refusal == nil)
    #expect(preview.blockers.isEmpty)
    #expect(preview.items.map(\.id) == ["history_rows", "history_lines", "session_index", "queue"])
    #expect(preview.items.map(\.count) == [2, 1, 1, 2])
    #expect(preview.items[1].lines == 3)
    #expect(preview.items[0].lines == nil)
    #expect(preview.items.allSatisfy { $0.bytes == nil && $0.checked })
    #expect(preview.items[0].label == "대화 저장소에 남은 내용")
    #expect(preview.vacuumAvailable)
    #expect(preview.historyDbBytes > 0)
}

@Test func cleanupPreviewDecodesARefusal() throws {
    let preview = try UsageJSON.decoder().decode(CleanupPreview.self, from: fixture("cleanup-dry-run-refusal"))
    #expect(preview.items.isEmpty)
    #expect(preview.refusal == CLIError(code: "codex_version", message: "Codex is migrating rollouts"))
    #expect(preview.vacuumAvailable == false)
}

@Test func cleanupReportDecodesVacuum() throws {
    let report = try UsageJSON.decoder().decode(CleanupReport.self, from: fixture("cleanup-yes-report"))
    #expect(report.stopped == false)
    #expect(report.items.map(\.id) == ["history_rows", "history_lines", "session_index", "queue", "cache_files"])
    #expect(report.items.allSatisfy { $0.outcome == "done" })
    #expect(report.items[1].lines == 2)
    #expect(report.items.map(\.count) == [1, 1, 1, 3, 1])
    let vacuum = try #require(report.vacuum)
    #expect(vacuum.stoppedBecause == "freelist_empty")
    #expect(vacuum.bytesBefore >= vacuum.bytesAfter)
    #expect(vacuum.message == nil)
}

@Test func cleanupReportDecodesStopsAndFailures() throws {
    // A Codex that started: items skipped. `stopped` stays false (SIGTERM only).
    let blocked = try decodeSession(CleanupReport.self, """
    {"items": [{"id": "history_rows", "outcome": "skipped", "count": 0, "reason": "codex_started"},
               {"id": "cache_files", "outcome": "skipped", "count": 0, "reason": "codex_started"}],
     "vacuum": {"stopped_because": "blocker", "bytes_before": 10, "bytes_after": 10}, "stopped": false}
    """)
    #expect(blocked.items.map(\.reason) == ["codex_started", "codex_started"])
    #expect(blocked.stopped == false)
    #expect(blocked.vacuum?.stoppedBecause == "blocker")

    let failed = try decodeSession(CleanupReport.self, """
    {"items": [{"id": "queue", "outcome": "failed", "count": 0, "reason": "error", "message": "database is locked"}],
     "vacuum": {"stopped_because": "error", "message": "boom", "bytes_before": 7, "bytes_after": 7, "new": 1},
     "stopped": true}
    """)
    #expect(failed.items[0].message == "database is locked")
    #expect(failed.vacuum?.message == "boom")
    #expect(failed.stopped)
}

@Test func anUnknownStateStillDecodes() throws {
    let list = try decodeSession(SessionList.self, """
    {"tool": "claude", "checked_at": "2026-10-25T03:04:05Z", "agents_error": "process_scan_failed",
     "totals": {"sessions": 1, "bytes": 5, "running": 0}, "extra": true,
     "groups": [{"key": "k", "name": "n", "folders": 1, "folders_deleted": 0, "sessions": 1, "bytes": 5,
                 "running": 0, "items": [{"id": "a", "title": "t", "folder": "f", "cwd": "/c",
                 "last_activity": "2026-10-25T03:04:05Z", "bytes": 5, "state": "paused", "reason": "new_reason",
                 "place_reason": "new_place", "surprise": 1}]}]}
    """)
    #expect(list.agentsError == "process_scan_failed")
    #expect(list.groups[0].items[0].state == "paused")
    #expect(list.groups[0].items[0].placeReason == "new_place")
}

@Test func logEntriesDecode() throws {
    let cleanup = try UsageJSON.decoder().decode(SessionLogEntry.self, from: fixture("session-log-cleanup"))
    #expect((cleanup.command, cleanup.tool) == ("cleanup", "codex"))
    #expect(cleanup.startedAt <= cleanup.finishedAt)
    guard case let .cleanup(report)? = cleanup.result else {
        Issue.record("expected a cleanup report, got \(String(describing: cleanup.result))")
        return
    }
    #expect(report.items.last?.id == "cache_files")
    #expect(report.vacuum?.stoppedBecause == "freelist_empty")

    let delete = try UsageJSON.decoder().decode(SessionLogEntry.self, from: fixture("session-log-delete"))
    #expect((delete.command, delete.tool) == ("delete", "claude"))
    guard case let .delete(deleted)? = delete.result else {
        Issue.record("expected a delete report, got \(String(describing: delete.result))")
        return
    }
    #expect(deleted.results.count == 4)
}

@Test func aLogEntryWithoutAReportStillDecodes() throws {
    let refused = try decodeSession(SessionLogEntry.self, """
    {"command": "cleanup", "tool": "codex", "started_at": "2026-10-25T03:04:05Z",
     "finished_at": "2026-10-25T03:04:06Z",
     "result": {"stopped": false, "ids": {}, "error": {"code": "codex_running", "message": "quit it"}}}
    """)
    #expect(refused.result == nil)
    #expect(refused.command == "cleanup")
    let busy = try decodeSession(SessionLogEntry.self, """
    {"command": "recover", "tool": "claude", "started_at": "2026-10-25T03:04:05Z",
     "finished_at": "2026-10-25T03:04:06Z", "result": {"error": {"code": "busy", "message": "x"}}}
    """)
    #expect(busy.result == nil)
}
