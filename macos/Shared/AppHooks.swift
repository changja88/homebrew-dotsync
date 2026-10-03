import Foundation

/// Work only the app process may do (open its window, quit), plugged in at
/// app launch. The widget extension never sets these.
@MainActor
enum AppHooks {
    static var open: ((URL) -> Void)?
    static var afterIntent: (() -> Void)?
}

extension Notification.Name {
    /// usage.json changed outside the window's own actions (a widget button).
    static let usageFileChanged = Notification.Name("dotsync.usageFileChanged")
}
