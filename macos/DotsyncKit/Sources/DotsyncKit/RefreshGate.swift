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
}
