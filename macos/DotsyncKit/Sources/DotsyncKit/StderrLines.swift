import Foundation

/// Splits stderr, which arrives in chunks that end anywhere, into lines. A
/// line that is exactly `dotsync-progress <done>/<total>`, with an optional
/// phase word after it (cleanup's `read`, `delete`, `vacuum`), is progress;
/// every other line is kept (with its newline) for the error message.
struct StderrLines {
    typealias Progress = (done: Int, total: Int, phase: String?)

    private var pending = Data()
    private(set) var kept = Data()

    mutating func feed(_ chunk: Data) -> [Progress] {
        pending.append(chunk)
        var steps: [Progress] = []
        while let newline = pending.firstIndex(of: UInt8(ascii: "\n")) {
            if let step = take(pending[pending.startIndex..<newline]) { steps.append(step) }
            pending.removeSubrange(pending.startIndex...newline)
        }
        return steps
    }

    /// The end of stderr: a last line without its newline is a whole line too.
    mutating func finish() -> [Progress] {
        defer { pending = Data() }
        return pending.isEmpty ? [] : take(pending).map { [$0] } ?? []
    }

    private mutating func take(_ line: Data) -> Progress? {
        if let step = Self.progress(in: String(decoding: line, as: UTF8.self)) { return step }
        kept.append(line)
        kept.append(UInt8(ascii: "\n"))
        return nil
    }

    static func progress(in line: String) -> Progress? {
        let prefix = "dotsync-progress "
        guard line.hasPrefix(prefix) else { return nil }
        let words = line.dropFirst(prefix.count).split(separator: " ", omittingEmptySubsequences: false)
        guard words.count <= 2 else { return nil }
        var phase: String?
        if words.count == 2 {
            guard !words[1].isEmpty, words[1].utf8.allSatisfy({ (97...122).contains($0) }) else { return nil }
            phase = String(words[1])
        }
        let numbers = words[0].split(separator: "/", omittingEmptySubsequences: false)
        guard numbers.count == 2,
              numbers.allSatisfy({ !$0.isEmpty && $0.utf8.allSatisfy { (48...57).contains($0) } }),
              let done = Int(numbers[0]), let total = Int(numbers[1]) else { return nil }
        return (done, total, phase)
    }
}
