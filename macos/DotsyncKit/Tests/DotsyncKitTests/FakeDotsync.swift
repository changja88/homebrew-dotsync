import Foundation
@testable import DotsyncKit

/// A stand-in `dotsync`: a shell script whose body decides the answer. It
/// records each call's arguments (one per line, then "---") in `log`.
struct FakeDotsync {
    let directory: URL
    let executable: URL
    let log: URL

    init(script body: String) throws {
        directory = try temporaryDirectory()
        executable = directory.appendingPathComponent("dotsync")
        log = directory.appendingPathComponent("calls.log")
        let script = """
        #!/bin/sh
        printf '%s\\n' "$@" >> '\(log.path)'
        echo --- >> '\(log.path)'
        \(body)
        """
        try Data(script.utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
    }

    var cli: DotsyncCLI {
        DotsyncCLI(executable: executable, environment: ["PATH": "/usr/bin:/bin"])
    }

    /// Writes `text` to a file next to the script and returns its path,
    /// for `cat` in a script body.
    func file(_ name: String, _ text: String) throws -> String {
        let url = directory.appendingPathComponent(name)
        try Data(text.utf8).write(to: url)
        return url.path
    }

    func calls() -> [[String]] {
        guard let text = try? String(contentsOf: log, encoding: .utf8) else { return [] }
        return text.components(separatedBy: "---\n").filter { !$0.isEmpty }.map {
            $0.split(separator: "\n", omittingEmptySubsequences: false).dropLast().map(String.init)
        }
    }

    // A test that signals or samples a running fake waits for it to be ready,
    // not for its first line: the call is logged before any `trap` is set,
    // and a busy test pool can resume only after a short fake has finished.

    /// Script lines that mark the fake ready. After a `trap`, so a SIGTERM
    /// the test sends finds it set.
    static let ready = #": > "$0.ready""#

    /// `ready`, then wait until the test calls `letGo()`: the fake cannot
    /// finish before the test has looked. A test that never lets it go (it
    /// threw first) does not hang the suite: after `seconds` the fake exits 1.
    static func readyThenWait(givingUpAfter seconds: Int = 30) -> String {
        """
        : > "$0.ready"
        tries=0
        while [ ! -e "$0.go" ]; do
          tries=$((tries + 1)); [ $tries -gt \(seconds * 20) ] && exit 1
          sleep 0.05
        done
        """
    }

    /// Throws `FakeNotReady` (the test's issue) if the fake is not ready by
    /// `timeout`.
    func waitUntilReady(timeout: Duration = .seconds(10)) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while !FileManager.default.fileExists(atPath: executable.path + ".ready") {
            guard clock.now < deadline else { throw FakeNotReady(what: "the fake did not get ready") }
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    func letGo() {
        FileManager.default.createFile(atPath: executable.path + ".go", contents: nil)
    }
}

/// A gated fake's wait ran out; thrown, it fails the test that waited.
struct FakeNotReady: Error, CustomStringConvertible {
    var what: String
    var description: String { "\(what) in time" }
}
