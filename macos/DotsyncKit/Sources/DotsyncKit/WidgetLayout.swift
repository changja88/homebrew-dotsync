import Foundation

/// What the large widget shows (approved mockup v16 ③): the account in use
/// on top, the others below in usage order, the rest as "외 n개".
public struct WidgetLayout: Equatable, Sendable {
    public enum RowKind: Equatable, Sendable {
        case metrics
        /// Weekly use 98 %+: only "주간 한도 다 씀 · <date> 초기화".
        case fullWeek(Date)
        case loginLost
    }

    /// Rows that fit under the in-use card on the large widget.
    public static let maxRows = 4

    public var current: AccountUsage?
    public var unsavedSeat: UnsavedSeat?
    public var rows: [AccountUsage]
    public var hidden: Int
    /// Claude is on a login no saved account holds: switching from the
    /// widget would drop it, so "사용" opens the app's confirm sheet instead.
    public var switchNeedsConfirmation: Bool

    public init(file: UsageFile, now: Date, maxRows: Int = WidgetLayout.maxRows) {
        current = file.activeAccount
        unsavedSeat = current == nil ? file.unsavedSeat : nil
        let others = UsageOrder.sorted(file.accounts.filter { $0.name != file.active })
        rows = Array(others.prefix(maxRows))
        hidden = max(0, others.count - maxRows)
        switchNeedsConfirmation = unsavedSeat != nil
    }

    public static func kind(of account: AccountUsage, now: Date) -> RowKind {
        if account.status == .loginRequired { return .loginLost }
        if UsageText.isFull(account.sevenDay, now: now), let reset = account.sevenDay?.resetsAt {
            return .fullWeek(reset)
        }
        return .metrics
    }

    /// One timeline entry a minute for an hour, so "n분 후 초기화" counts down
    /// without running dotsync.
    public static func minuteSchedule(from start: Date, count: Int = 60) -> [Date] {
        (0..<count).map { start.addingTimeInterval(TimeInterval($0 * 60)) }
    }
}
