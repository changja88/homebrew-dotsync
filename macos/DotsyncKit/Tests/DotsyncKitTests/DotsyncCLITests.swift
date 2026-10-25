import Foundation
import Testing
@testable import DotsyncKit

@Test func usageDecodesTheReport() async throws {
    let fake = try FakeDotsync(script: "")
    let path = try fake.file("usage.json", sampleReport)
    let real = try FakeDotsync(script: "cat '\(path)'")
    let report = try await real.cli.usage()
    #expect(report.active == "onelife")
    #expect(real.calls() == [["account", "usage", "--json"]])
}

@Test func positionalsGoAfterADoubleDash() async throws {
    let fake = try FakeDotsync(script: #"echo '{"name": "x", "label": "x", "email": null, "logged_in": true}'"#)
    _ = try await fake.cli.use("-odd", overwriteUnsaved: true)
    _ = try await fake.cli.rename("bob", to: "밥 업무용")
    _ = try await fake.cli.login("new")
    try await fake.cli.remove("gone")
    #expect(fake.calls() == [
        ["account", "use", "--json", "--yes", "--", "-odd"],
        ["account", "rename", "--json", "--", "bob", "밥 업무용"],
        ["account", "login", "--json", "--", "new"],
        ["account", "remove", "--json", "--", "gone"],
    ])
}

@Test func errorsComeBackAsCLIErrors() async throws {
    let fake = try FakeDotsync(script: """
    echo '{"error": {"code": "unsaved_login", "message": "not saved", "email": "u@x"}}'
    exit 1
    """)
    await #expect(throws: CLIError(code: "unsaved_login", message: "not saved", email: "u@x")) {
        _ = try await fake.cli.use("bob", overwriteUnsaved: false)
    }
}

@Test func anAnswerThatIsNotJSONIsAFailureWithStderrsLastLine() async throws {
    let fake = try FakeDotsync(script: """
    echo 'usage: dotsync account ...' >&2
    echo 'dotsync: error: the following arguments are required: label' >&2
    exit 2
    """)
    await #expect(throws: CLIError(code: "failed", message: "dotsync: error: the following arguments are required: label")) {
        _ = try await fake.cli.rename("bob", to: "x")
    }
}

@Test func anUnexpectedSuccessAnswerIsAFailure() async throws {
    let fake = try FakeDotsync(script: "echo 'hello'")
    await #expect(throws: CLIError.self) { _ = try await fake.cli.usage() }
}

@Test func aMissingExecutableIsAFailure() async throws {
    let cli = DotsyncCLI(executable: URL(fileURLWithPath: "/nonexistent/dotsync"))
    await #expect(throws: CLIError.self) { _ = try await cli.usage() }
}

@Test func lotsOfStderrDoesNotStall() async throws {
    let fake = try FakeDotsync(script: """
    head -c 200000 /dev/zero | tr '\\0' 'x' >&2
    echo '{"name": "x", "label": "x", "email": null, "logged_in": true}'
    """)
    let info = try await fake.cli.login("x")
    #expect(info.name == "x")
}

@Test func cancellingALoginSendsSIGTERM() async throws {
    // The sleeping child must not hold the output pipes, or reading them
    // would wait for it; the trap stops it like dotsync stops `claude`.
    let fake = try FakeDotsync(script: """
    sleep 30 >/dev/null 2>&1 &
    child=$!
    trap 'kill $child; echo "{\\"error\\": {\\"code\\": \\"cancelled\\", \\"message\\": \\"login cancelled\\"}}"; exit 1' TERM
    wait $child
    """)
    let login = Task { try await fake.cli.login("slow") }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    login.cancel()
    await #expect(throws: CLIError(code: "cancelled", message: "login cancelled")) {
        _ = try await login.value
    }
}

@Test func cancellingAUseLetsDotsyncFinish() async throws {
    // `use` writes the Keychain, then Claude's files; stopped between the two,
    // the next switch would hand one account's login to another. A cancelled
    // caller (a widget button the system gave up on) waits for the answer.
    let fake = try FakeDotsync(script: """
    trap 'echo stopped >&2; exit 1' TERM
    sleep 0.5
    echo '{"name": "work", "label": "work", "email": null, "logged_in": true}'
    """)
    let use = Task { try await fake.cli.use("work", overwriteUnsaved: false) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    use.cancel()
    let info = try await use.value
    #expect(info.name == "work")
}

@Test func aCommandThatHangsIsStoppedAtTheTimeout() async throws {
    let fake = try FakeDotsync(script: "sleep 5")
    let clock = ContinuousClock()
    let started = clock.now
    await #expect(throws: CLIError(code: "timeout", message: "dotsync가 제때 끝나지 않았어요")) {
        _ = try await fake.cli.runJSON(["status", "--json"], timeout: .milliseconds(300))
    }
    #expect(clock.now - started < .seconds(2))
}

@Test func runJSONPassesArgumentsAsIs() async throws {
    let fake = try FakeDotsync(script: #"echo '{"dir": "/x"}'"#)
    let data = try await fake.cli.runJSON(["status", "--json"], timeout: .seconds(10))
    #expect(String(decoding: data, as: UTF8.self) == "{\"dir\": \"/x\"}\n")
    #expect(fake.calls() == [["status", "--json"]])
}

@Test func locatePrefersTheDevelopmentPath() throws {
    let fake = try FakeDotsync(script: "")
    let defaults = UserDefaults(suiteName: "dotsynckit-\(UUID().uuidString)")!
    defaults.set(fake.executable.path, forKey: "cliPath")
    #expect(DotsyncCLI.locate(defaults: defaults) == fake.executable)
    defaults.set("/nonexistent/dotsync", forKey: "cliPath")
    #expect(DotsyncCLI.locate(defaults: defaults) != URL(fileURLWithPath: "/nonexistent/dotsync"))
}

@Test func defaultEnvironmentPutsHomebrewFirst() {
    let env = DotsyncCLI.defaultEnvironment(["PATH": "/usr/bin:/bin", "HOME": "/Users/x"])
    #expect(env["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    #expect(env["HOME"] == "/Users/x")
}

// Progress lines and the registry of long runs

@Test func runJSONReportsProgressLines() async throws {
    let fake = try FakeDotsync(script: """
    echo 'dotsync-progress 1/3' >&2
    echo 'dotsync-progress 2/3' >&2
    echo '{"name": "x", "label": "x", "email": null, "logged_in": true}'
    """)
    let log = ProgressLog()
    let data = try await fake.cli.runJSON(["sessions", "x", "--json"], timeout: .seconds(10),
                                          progress: { log.add($0, $1) })
    #expect(log.all == [[1, 3], [2, 3]])
    #expect(try fake.cli.decode(AccountInfo.self, data).name == "x")
}

@Test func onlyAWholeProgressLineCallsProgress() throws {
    func progress(_ line: String) -> [Int]? {
        StderrLines.progress(in: line).map { [$0.done, $0.total] }
    }
    #expect(progress("dotsync-progress 0/0") == [0, 0])
    #expect(progress("dotsync-progress 12/345") == [12, 345])
    for other in ["", "dotsync-progress", "dotsync-progress 1/", "dotsync-progress /3", "dotsync-progress 1/3 ",
                  " dotsync-progress 1/3", "dotsync-progress 1/3/4", "dotsync-progress -1/3",
                  "dotsync-progress +1/3", "dotsync-progress 1/x", "dotsync-progress 1.5/3",
                  "dotsync-progress  1/3", "xdotsync-progress 1/3", "dotsync-progress 1/3\r",
                  "dotsync-progress 99999999999999999999/3", "warning: dotsync-progress 1/3"] {
        #expect(progress(other) == nil, "\(other.debugDescription)")
    }
}

@Test func aProgressLineSplitAcrossChunksIsNotLostOrMisread() {
    var lines = StderrLines()
    var seen: [[Int]] = []
    let chunks = ["dotsync-prog", "ress 2/", "5\nwarn", "ing: 못\n", "dotsync-progress 3/5\ndotsync-progress 4", "/5"]
    for chunk in chunks {
        seen += lines.feed(Data(chunk.utf8)).map { [$0.done, $0.total] }
    }
    // The last line has no newline yet: not progress until stderr ends.
    #expect(seen == [[2, 5], [3, 5]])
    seen += lines.finish().map { [$0.done, $0.total] }
    #expect(seen == [[2, 5], [3, 5], [4, 5]])
    #expect(String(decoding: lines.kept, as: UTF8.self) == "warning: 못\n")
}

@Test func aMultiByteCharacterSplitAcrossChunksIsKept() {
    var lines = StderrLines()
    let bytes = Array("조회 실패\n".utf8)
    _ = lines.feed(Data(bytes[0..<4]))
    _ = lines.feed(Data(bytes[4...]))
    #expect(String(decoding: lines.kept, as: UTF8.self) == "조회 실패\n")
}

@Test func anUnfinishedLastLineIsKeptWhenStderrEnds() {
    var lines = StderrLines()
    _ = lines.feed(Data("first\nlast words".utf8))
    _ = lines.finish()
    #expect(String(decoding: lines.kept, as: UTF8.self) == "first\nlast words\n")
}

/// A fake that stops like dotsync does: TERM makes it finish what it is
/// doing for `finishing` seconds, then answer `{}`.
private func stubbornFake(finishing seconds: String) throws -> FakeDotsync {
    try FakeDotsync(script: """
    sleep 30 >/dev/null 2>&1 &
    child=$!
    trap 'kill $child; sleep \(seconds); echo "{}"; exit 0' TERM
    wait $child
    """)
}

private func waitUntilRunning(_ fake: FakeDotsync, _ registry: RunningCommands) async throws {
    while fake.calls().isEmpty || registry.count == 0 { try await Task.sleep(for: .milliseconds(20)) }
}

@Test func terminateAllStopsLongRuns() async throws {
    let registry = RunningCommands()
    let fake = try stubbornFake(finishing: "0.5")
    let cli = DotsyncCLI(executable: fake.executable, environment: fake.cli.environment, runningCommands: registry)
    let run = Task { try await cli.runJSON(["sessions", "delete"], timeout: .seconds(60), longRun: true) }
    try await waitUntilRunning(fake, registry)

    let clock = ContinuousClock()
    let started = clock.now
    let allExited = registry.terminateAll(waitingUpTo: .seconds(5))
    #expect(allExited)
    #expect(clock.now - started < .seconds(2))
    #expect(String(decoding: try await run.value, as: UTF8.self) == "{}\n")
    #expect(registry.count == 0)
}

@Test func terminateAllGivesUpAtTheLimit() async throws {
    let registry = RunningCommands()
    let fake = try FakeDotsync(script: "trap '' TERM; sleep 1.5; echo '{}'")
    let cli = DotsyncCLI(executable: fake.executable, environment: fake.cli.environment, runningCommands: registry)
    let run = Task { try await cli.runJSON(["x"], timeout: .seconds(60), longRun: true) }
    try await waitUntilRunning(fake, registry)

    let clock = ContinuousClock()
    let started = clock.now
    #expect(registry.terminateAll(waitingUpTo: .milliseconds(300)) == false)
    #expect(clock.now - started < .seconds(1))
    _ = try await run.value
}

@Test func terminateAllCountsARunThatEndsOnItsOwn() async throws {
    let registry = RunningCommands()
    let fake = try FakeDotsync(script: "trap '' TERM; sleep 0.4; echo '{}'")
    let cli = DotsyncCLI(executable: fake.executable, environment: fake.cli.environment, runningCommands: registry)
    let run = Task { try await cli.runJSON(["x"], timeout: .seconds(60), longRun: true) }
    try await waitUntilRunning(fake, registry)

    #expect(registry.terminateAll(waitingUpTo: .seconds(5)))
    _ = try await run.value
    #expect(registry.count == 0)
}

@Test func terminateAllWithNothingRunningIsImmediate() {
    #expect(RunningCommands().terminateAll(waitingUpTo: .seconds(5)))
}

@Test func onlyLongRunsAreRegistered() async throws {
    let registry = RunningCommands()
    let fake = try FakeDotsync(script: "sleep 0.3; echo '{}'")
    let cli = DotsyncCLI(executable: fake.executable, environment: fake.cli.environment, runningCommands: registry)
    let run = Task { try await cli.runJSON(["x"], timeout: .seconds(60)) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    #expect(registry.count == 0)
    _ = try await run.value
}

@Test func terminateAllReturnsTrueOnlyAfterTheChildHasExited() async throws {
    let registry = RunningCommands()
    let fake = try FakeDotsync(script: """
    sleep 30 >/dev/null 2>&1 &
    child=$!
    trap 'kill $child; sleep 0.5; echo exited > "$0.exited"; echo "{}"; exit 0' TERM
    wait $child
    """)
    let marker = fake.executable.path + ".exited"
    let cli = DotsyncCLI(executable: fake.executable, environment: fake.cli.environment, runningCommands: registry)
    let run = Task { try await cli.runJSON(["x"], timeout: .seconds(60), longRun: true) }
    try await waitUntilRunning(fake, registry)

    #expect(registry.terminateAll(waitingUpTo: .seconds(5)))
    // Before awaiting the run: the child's last act was in place already.
    #expect(FileManager.default.fileExists(atPath: marker))
    _ = try await run.value
}
