import Foundation

/// The long runs (delete, recover, cleanup) that are going on now, so the app
/// can stop them when the Mac powers off.
public final class RunningCommands: @unchecked Sendable {
    public static let shared = RunningCommands()

    private let lock = NSLock()
    private var processes: [ObjectIdentifier: Process] = [:]

    public init() {}

    var count: Int {
        lock.withLock { processes.count }
    }

    func add(_ process: Process) {
        lock.withLock { processes[ObjectIdentifier(process)] = process }
    }

    func remove(_ process: Process) {
        _ = lock.withLock { processes.removeValue(forKey: ObjectIdentifier(process)) }
    }

    /// Sends SIGTERM to each and polls every 100 ms until all have exited or
    /// `limit` has passed. Returns whether all exited. The lock is not held
    /// while waiting: a run that ends on its own removes itself meanwhile.
    public func terminateAll(waitingUpTo limit: Duration) -> Bool {
        let running = lock.withLock { Array(processes.values) }
        for process in running where process.isRunning {
            process.terminate()
        }
        let clock = ContinuousClock()
        let deadline = clock.now + limit
        while running.contains(where: \.isRunning) {
            if clock.now >= deadline { return false }
            Thread.sleep(forTimeInterval: 0.1)
        }
        return true
    }
}
