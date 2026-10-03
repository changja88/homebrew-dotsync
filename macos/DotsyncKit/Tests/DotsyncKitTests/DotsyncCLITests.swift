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
