import Foundation

/// Where a long run is, from the CLI's progress lines. A cleanup goes through
/// three phases (`read`, `delete`, `vacuum`), each counting something else, so
/// a new phase starts the count again.
public struct RunProgress: Equatable, Sendable {
    public var done = 0
    public var total = 0
    public var phase: String?

    public init(done: Int = 0, total: Int = 0, phase: String? = nil) {
        self.done = done
        self.total = total
        self.phase = phase
    }

    /// The CLI has said something. Before that there is no count to show.
    public var hasCount: Bool { total > 0 }

    private static let phaseOrder = ["read", "delete", "vacuum"]

    /// Lines hop to the main actor one by one and may land out of order: within
    /// a phase `done` only grows, and a line of an earlier phase than the
    /// current one is dropped.
    public mutating func advance(done newDone: Int, total newTotal: Int, phase newPhase: String?) {
        if newPhase != phase {
            if let now = phase.flatMap(Self.phaseOrder.firstIndex(of:)),
               let then = newPhase.flatMap(Self.phaseOrder.firstIndex(of:)), then < now { return }
            self = RunProgress(done: newDone, total: max(newTotal, 1), phase: newPhase)
            return
        }
        done = max(done, newDone)
        total = max(newTotal, 1)
    }

    /// "12/240" for delete and recover; a cleanup phase says what it counts.
    public var text: String {
        phase == nil ? SessionText.progress(done: done, total: total) : SessionText.progressLine(phase: phase, done: done, total: total)
    }
}
