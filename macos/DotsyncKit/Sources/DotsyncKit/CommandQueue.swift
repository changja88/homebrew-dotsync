import Observation

/// The window's dotsync commands, one at a time (spec Part 4: a serial
/// queue). One asked for while another runs waits its turn: dropping it lost
/// "저장 안 하고 바꾸기" pressed during a refresh, and the switch after
/// "먼저 저장하기…", which always lands in the refresh the login starts.
@MainActor
@Observable
public final class CommandQueue {
    /// What runs now, for the window to show; nil when idle.
    public private(set) var running: String?
    @ObservationIgnored private var waiting: [CheckedContinuation<Void, Never>] = []

    public init() {}

    public func run(_ label: String, _ work: () async -> Void) async {
        if running != nil {
            await withCheckedContinuation { waiting.append($0) }
        }
        running = label
        await work()
        if waiting.isEmpty {
            running = nil
        } else {
            // Straight to the next one, so no newcomer slips in between.
            waiting.removeFirst().resume()
        }
    }
}
