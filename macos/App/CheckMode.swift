import AppKit
import DotsyncKit
import os

/// What launchd runs on every change in the app group folder: read
/// usage.json, live-usage.json and auto-switch.json, decide, and wake the app
/// only when it has work. It never starts NSApplication and writes nothing
/// to the folder — a write would wake the check again.
enum CheckMode {
    static func run() -> Int32 {
        guard let usage = UsageStore.shared(), let autoSwitch = AutoSwitchStore.shared() else { return 0 }
        let decision = AutoSwitchRules.decide(autoSwitch.loadSettings(), file: usage.load() ?? .empty,
                                              live: usage.loadLive(), now: Date())
        switch decision {
        case .none: return 0
        case .switchNow: return wake(.switchNow)
        case .refetch: return wake(.refetch)
        }
    }

    /// Opens the auto-switch link in this copy of the app (spike 1c): hidden
    /// when it isn't running yet, and never brought to the front.
    private static func wake(_ request: AutoSwitchRequest) -> Int32 {
        let bundleID = Bundle.main.bundleIdentifier ?? AutoSwitchAgent.appBundleID
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .contains { $0.processIdentifier != getpid() }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.hides = !running
        let done = DispatchSemaphore(value: 0)
        let failed = OSAllocatedUnfairLock(initialState: false)
        NSWorkspace.shared.open([AppLink.autoSwitch(request).url], withApplicationAt: Bundle.main.bundleURL,
                                configuration: configuration) { _, error in
            if error != nil { failed.withLock { $0 = true } }
            done.signal()
        }
        guard done.wait(timeout: .now() + 10) == .success else { return 1 }
        return failed.withLock { $0 } ? 1 : 0
    }
}
