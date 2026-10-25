import Foundation

/// When a ChatGPT that was asked to quit has said no. `terminate()` only
/// sends the request: ChatGPT may decline it, or the person may cancel its
/// own confirmation.
public enum ChatGPTQuitRules {
    /// An instance still alive this long after the request was sent declined.
    public static let declineAfter: TimeInterval = 60

    /// `askedAt` is when `terminate()` was sent to an instance that is still
    /// alive. Within the window a slow quit is never counted as reopened.
    public static func declined(askedAt: Date, now: Date) -> Bool {
        now.timeIntervalSince(askedAt) > declineAfter
    }
}
