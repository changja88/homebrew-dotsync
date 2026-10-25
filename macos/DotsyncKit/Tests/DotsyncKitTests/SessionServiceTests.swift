import Foundation
import Testing
@testable import DotsyncKit

private let emptyDelete = #"{"tool": "claude", "results": [], "stopped": false}"#
private let emptyCleanup = #"{"items": [], "vacuum": null, "stopped": false}"#

/// Collects what the progress callback sees, from any thread.
final class ProgressLog: @unchecked Sendable {
    private let lock = NSLock()
    private var steps: [[Int]] = []

    func add(_ done: Int, _ total: Int) {
        lock.withLock { steps.append([done, total]) }
    }

    var all: [[Int]] { lock.withLock { steps } }
}

@Test func deleteArgvEndsWithIds() async throws {
    let fake = try FakeDotsync(script: "echo '\(emptyDelete)'")
    let service = SessionService(cli: fake.cli)
    _ = try await service.delete(.claude, ids: ["-odd-id", "b"])
    _ = try await service.delete(.codex, ids: ["c"])
    #expect(fake.calls() == [
        ["sessions", "delete", "claude", "--json", "--", "-odd-id", "b"],
        ["sessions", "delete", "codex", "--json", "--", "c"],
    ])
}

@Test func theOtherCommandsRunTheirArgv() async throws {
    let list = try fixture("claude-list")
    let listPath = try FakeDotsync(script: "").file("list.json", String(decoding: list, as: UTF8.self))
    let fake = try FakeDotsync(script: """
    case "$2" in
      list) cat '\(listPath)' ;;
      recover) echo '\(emptyDelete)' ;;
      cleanup) case "$4" in
        --blockers) echo '{"blockers": [], "chatgpt_helpers": [], "chatgpt_open_threads": 0}' ;;
        --dry-run) echo '{"blockers": [], "chatgpt_helpers": [], "chatgpt_open_threads": 0, "items": [], "history_db_bytes": 0, "vacuum_available": false, "refusal": null}' ;;
        *) echo '\(emptyCleanup)' ;;
      esac ;;
    esac
    """)
    let service = SessionService(cli: fake.cli)
    let sessions = try await service.list(.claude)
    #expect(sessions.tool == .claude)
    _ = try await service.recover("an-id", finish: true)
    _ = try await service.recover("an-id", finish: false)
    _ = try await service.blockers()
    _ = try await service.cleanupPreview()
    _ = try await service.cleanup(items: ["history_rows", "queue"], vacuum: true)
    _ = try await service.cleanup(items: ["session_index"], vacuum: false)
    #expect(fake.calls() == [
        ["sessions", "list", "claude", "--json"],
        ["sessions", "recover", "claude", "--json", "--finish", "--", "an-id"],
        ["sessions", "recover", "claude", "--json", "--restore", "--", "an-id"],
        ["sessions", "cleanup", "codex", "--blockers", "--json"],
        ["sessions", "cleanup", "codex", "--dry-run", "--json"],
        ["sessions", "cleanup", "codex", "--yes", "--json", "--items", "history_rows,queue", "--vacuum"],
        ["sessions", "cleanup", "codex", "--yes", "--json", "--items", "session_index"],
    ])
}

@Test func anErrorAnswerThrowsTheCLIError() async throws {
    let fake = try FakeDotsync(script: """
    echo '{"error": {"code": "busy", "message": "another delete is running"}}'
    exit 1
    """)
    await #expect(throws: CLIError(code: "busy", message: "another delete is running")) {
        _ = try await SessionService(cli: fake.cli).delete(.claude, ids: ["a"])
    }
}

@Test func deleteReportsProgressAndTheErrorKeepsOnlyTheOtherLines() async throws {
    let fake = try FakeDotsync(script: """
    echo 'dotsync-progress 1/2' >&2
    echo 'something went wrong' >&2
    echo 'dotsync-progress 2/2' >&2
    exit 3
    """)
    let log = ProgressLog()
    await #expect(throws: CLIError(code: "failed", message: "something went wrong")) {
        _ = try await SessionService(cli: fake.cli).delete(.codex, ids: ["a", "b"], progress: { log.add($0, $1) })
    }
    #expect(log.all == [[1, 2], [2, 2]])
}

@Test func cancelledDeleteReturnsWhatTheCLIDid() async throws {
    let fake = try FakeDotsync(script: """
    sleep 30 >/dev/null 2>&1 &
    child=$!
    trap 'kill $child; echo "{\\"tool\\": \\"claude\\", \\"results\\": [{\\"id\\": \\"a\\", \\"outcome\\": \\"deleted\\"}], \\"stopped\\": true}"; exit 0' TERM
    wait $child
    """)
    let service = SessionService(cli: fake.cli)
    let task = Task { try await service.delete(.claude, ids: ["a", "b"]) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    task.cancel()
    let report = try await task.value
    #expect(report.stopped)
    #expect(report.results.map(\.id) == ["a"])
}

private func writeLog(_ directory: URL, _ name: String, command: String, startedAt: String) throws {
    let json = """
    {"command": "\(command)", "tool": "claude", "started_at": "\(startedAt)",
     "finished_at": "\(startedAt)", "result": {"tool": "claude", "results": [], "stopped": false}}
    """
    try Data(json.utf8).write(to: directory.appendingPathComponent(name))
}

@Test func latestLogPicksNewestSince() throws {
    let directory = try temporaryDirectory()
    try writeLog(directory, "sessions-20261025T010000Z-1.json", command: "delete", startedAt: "2026-10-25T01:00:00Z")
    try writeLog(directory, "sessions-20261025T030000Z-2.json", command: "recover", startedAt: "2026-10-25T03:00:00Z")
    try writeLog(directory, "sessions-20261025T020000Z-3.json", command: "delete", startedAt: "2026-10-25T02:00:00Z")
    try Data("not json".utf8).write(to: directory.appendingPathComponent("sessions-20261025T040000Z-4.json"))
    try writeLog(directory, "other-5.json", command: "delete", startedAt: "2026-10-25T05:00:00Z")
    let formatter = ISO8601DateFormatter()
    let at = { (text: String) in formatter.date(from: text)! }

    #expect(SessionLog.latest(since: at("2026-10-25T00:00:00Z"), in: directory)?.command == "recover")
    #expect(SessionLog.latest(since: at("2026-10-25T03:00:00Z"), in: directory)?.command == "recover")
    #expect(SessionLog.latest(since: at("2026-10-25T03:00:01Z"), in: directory) == nil)
    #expect(SessionLog.latest(since: .distantPast, in: directory.appendingPathComponent("missing")) == nil)
}

// Task 22's power-off stop reaches only the runs registered as long runs.

private func slowFake() throws -> FakeDotsync {
    try FakeDotsync(script: """
    \(FakeDotsync.readyThenWait())
    case "$2" in
      list) echo '{"tool": "claude", "checked_at": "2026-10-25T03:04:05Z", "totals": {"sessions": 0, "bytes": 0, "running": 0}, "groups": []}' ;;
      delete|recover) echo '\(emptyDelete)' ;;
      cleanup) case "$4" in
        --blockers) echo '{"blockers": [], "chatgpt_helpers": [], "chatgpt_open_threads": 0}' ;;
        --dry-run) echo '{"blockers": [], "chatgpt_helpers": [], "chatgpt_open_threads": 0, "items": [], "history_db_bytes": 0, "vacuum_available": false, "refusal": null}' ;;
        *) echo '\(emptyCleanup)' ;;
      esac ;;
    esac
    """)
}

/// The commands the registry holds while `call`'s fake runs; the fake waits
/// to be let go, so the count is read while it still runs.
private func registered(during call: @escaping @Sendable (SessionService) async throws -> Void,
                        registry: RunningCommands, fake: FakeDotsync) async throws -> Int {
    let service = SessionService(cli: DotsyncCLI(executable: fake.executable, environment: fake.cli.environment,
                                                 runningCommands: registry))
    let run = Task { try await call(service) }
    try await fake.waitUntilReady()
    // Registering follows the spawn at once.
    try await Task.sleep(for: .milliseconds(50))
    let seen = registry.count
    fake.letGo()
    try await run.value
    #expect(registry.count == 0)
    return seen
}

@Test(.serialized, .timeLimit(.minutes(1)), arguments: [
    "delete", "recover", "cleanup", "list", "blockers", "cleanupPreview",
])
func onlyDeleteRecoverAndCleanupAreLongRuns(_ method: String) async throws {
    let registry = RunningCommands()
    let fake = try slowFake()
    let seen = try await registered(during: { service in
        switch method {
        case "delete": _ = try await service.delete(.claude, ids: ["a"])
        case "recover": _ = try await service.recover("a", finish: true)
        case "cleanup": _ = try await service.cleanup(items: ["queue"], vacuum: false)
        case "list": _ = try await service.list(.claude)
        case "blockers": _ = try await service.blockers()
        default: _ = try await service.cleanupPreview()
        }
    }, registry: registry, fake: fake)
    #expect(seen == (["delete", "recover", "cleanup"].contains(method) ? 1 : 0), "\(method)")
}
