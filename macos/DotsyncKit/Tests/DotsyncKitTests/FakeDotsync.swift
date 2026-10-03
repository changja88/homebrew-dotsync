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
}
