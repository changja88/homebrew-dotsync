import Foundation

/// One refresh at a time: a caller that arrives while one runs waits for it
/// and gets the same file. People press the widget's ↻ again while it works,
/// and launching the app for ↻ also starts the window's refresh.
public actor RefreshGate {
    /// The one the app's window and the widget's buttons share.
    public static let shared = RefreshGate()

    private var running: Task<UsageFile, Never>?

    public init() {}

    public func refresh(_ service: AccountService) async -> UsageFile {
        if let running { return await running.value }
        let task = Task { await service.refresh() }
        running = task
        let file = await task.value
        running = nil
        return file
    }

    /// Before the app quits: lets a running refresh finish and save, so the
    /// widget isn't left on 조회 중… without the result. Past `limit` it is
    /// cancelled instead, which puts usage.json back as it was.
    public func settle(within limit: Duration) async {
        guard let running else { return }
        let deadline = Task {
            try await Task.sleep(for: limit)
            running.cancel()
        }
        _ = await running.value
        deadline.cancel()
    }
}
