import Foundation

public enum UsageOrder {
    /// Most of the week left first (a week whose reset passed counts as
    /// unused); then the accounts whose week is used up, the one that resets
    /// soonest first; then those with no weekly figure (not read yet, logged
    /// out). Ties go to the sooner weekly reset, then the label.
    public static func sorted(_ accounts: [AccountUsage], now: Date) -> [AccountUsage] {
        accounts.sorted { a, b in
            let x = key(a, now: now)
            let y = key(b, now: now)
            if x.group != y.group { return x.group < y.group }
            if x.used != y.used { return x.used < y.used }
            if x.reset != y.reset { return x.reset < y.reset }
            return a.label < b.label
        }
    }

    private static func key(_ account: AccountUsage, now: Date) -> (group: Int, used: Int, reset: Date) {
        guard account.status != .loginRequired, let week = account.sevenDay else {
            return (2, 0, .distantFuture)
        }
        let reset = week.resetsAt ?? .distantFuture
        if UsageText.isFull(week, now: now) { return (1, 0, reset) }
        return (0, UsageText.isReset(week, now: now) ? 0 : week.percent, reset)
    }
}
