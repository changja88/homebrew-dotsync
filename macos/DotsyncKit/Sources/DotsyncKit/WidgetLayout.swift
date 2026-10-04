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
        let others = UsageOrder.sorted(file.accounts.filter { $0.name != file.active }, now: now)
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

    /// A timeline covers a day; WidgetKit asks for the next one after it.
    public static let timelineSpan: TimeInterval = 86_400

    /// When the widget looks different without new data: now, each reset in
    /// the coming day (its countdown turns into 초기화됨) and the end of a 조회 중
    /// mark an app left behind. Remaining times count down by themselves, so a
    /// handful of entries replaces one a minute — WidgetKit renders every
    /// entry on each reload, and sixty of them took seconds and got too big.
    public static func timelineDates(for file: UsageFile, now: Date) -> [Date] {
        let windows = file.accounts.flatMap { [$0.fiveHour, $0.sevenDay] }
            + [file.unsavedSeat?.fiveHour, file.unsavedSeat?.sevenDay]
        var changes = windows.compactMap { $0?.resetsAt }
        if file.isRefreshing(at: now), let start = file.refreshingSince {
            changes.append(start.addingTimeInterval(UsageFile.refreshLimit))
        }
        let end = now.addingTimeInterval(timelineSpan)
        return [now] + Set(changes.filter { $0 > now && $0 < end }).sorted()
    }
}
