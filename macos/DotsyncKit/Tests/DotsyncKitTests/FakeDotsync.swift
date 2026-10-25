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
    /// finish before the test has looked.
    static let readyThenWait = """
    : > "$0.ready"
    while [ ! -e "$0.go" ]; do sleep 0.05; done
    """

    func waitUntilReady() async throws {
        while !FileManager.default.fileExists(atPath: executable.path + ".ready") {
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    func letGo() {
        FileManager.default.createFile(atPath: executable.path + ".go", contents: nil)
    }
}
