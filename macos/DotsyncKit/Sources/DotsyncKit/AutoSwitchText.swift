import Foundation

/// A macOS notification's two lines.
public struct AutoSwitchNotice: Equatable, Sendable {
    public var title: String
    public var body: String

    public init(title: String, body: String) {
        self.title = title
        self.body = body
    }
}

/// What auto switch says, in the notifications and the 자동 교체 menu.
public enum AutoSwitchText {
    /// A switch, or the first time no account can take over (spec 5.6).
    /// `previous` is the log entry before `entry`: a full house repeats every
    /// 15 minutes, and one notification is enough. Names show as labels.
    public static func notification(for entry: AutoSwitchLogEntry, previous: AutoSwitchLogEntry?,
                                    labels: [String: String]) -> AutoSwitchNotice? {
        switch entry.result {
        case .switched:
            let from = entry.from.map { labels[$0] ?? $0 } ?? ""
            let to = entry.to.map { labels[$0] ?? $0 } ?? ""
            return AutoSwitchNotice(title: "자동 교체", body: "\(from) → \(to) (\(entry.reason ?? ""))")
        case .noCandidate where previous?.result != .noCandidate:
            return AutoSwitchNotice(title: "자동 교체", body: "바꿀 계정이 없어요 — 모든 계정이 기준을 넘었어요")
        default:
            return nil
        }
    }
}
