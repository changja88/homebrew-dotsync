import Foundation

public enum UsageOrder {
    /// Weekly reset soonest first, so the account that frees up next comes
    /// first; accounts without a weekly reset time (not read yet, logged out)
    /// last; ties by label.
    public static func sorted(_ accounts: [AccountUsage]) -> [AccountUsage] {
        accounts.sorted { a, b in
            switch (weeklyReset(a), weeklyReset(b)) {
            case let (x?, y?): return x != y ? x < y : a.label < b.label
            case (.some, nil): return true
            case (nil, .some): return false
            case (nil, nil): return a.label < b.label
            }
        }
    }

    static func weeklyReset(_ account: AccountUsage) -> Date? {
        account.status == .loginRequired ? nil : account.sevenDay?.resetsAt
    }
}
