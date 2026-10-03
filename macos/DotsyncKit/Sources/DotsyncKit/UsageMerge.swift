import Foundation

/// How usage.json changes. The spec's merge rule: a probe that failed keeps
/// the account's last good values (and their time); a lost login clears them.
public enum UsageMerge {
    public static func merge(previous: UsageFile?, report: UsageReport) -> UsageFile {
        let before = Dictionary(
            (previous?.accounts ?? []).map { ($0.name, $0) }, uniquingKeysWith: { first, _ in first })
        let accounts = report.accounts.map { account -> AccountUsage in
            var merged = account
            switch account.status {
            case .ok:
                merged.fetchedAt = report.fetchedAt
            case .loginRequired:
                merged.fiveHour = nil
                merged.sevenDay = nil
                merged.fetchedAt = nil
            case .error:
                if let old = before[account.name], old.fetchedAt != nil {
                    merged.fiveHour = old.fiveHour
                    merged.sevenDay = old.sevenDay
                    merged.fetchedAt = old.fetchedAt
                } else {
                    merged.fetchedAt = nil
                }
            }
            return merged
        }
        return UsageFile(
            version: 1, fetchedAt: report.fetchedAt, lastError: nil, active: report.active,
            unsavedSeat: report.unsavedSeat, accounts: accounts)
    }

    /// The whole refresh failed: keep what was shown and say why.
    public static func failed(previous: UsageFile?, message: String) -> UsageFile {
        var file = previous ?? .empty
        file.lastError = message
        return file
    }

    /// Claude now uses `name`; its usage comes with the next refresh.
    public static func switched(_ file: UsageFile, to name: String) -> UsageFile {
        var copy = file
        copy.active = name
        copy.unsavedSeat = nil
        copy.lastError = nil
        return copy
    }

    public static func renamed(_ file: UsageFile, _ info: AccountInfo) -> UsageFile {
        var copy = file
        for index in copy.accounts.indices where copy.accounts[index].name == info.name {
            copy.accounts[index].label = info.label
        }
        return copy
    }

    public static func removed(_ file: UsageFile, _ name: String) -> UsageFile {
        var copy = file
        copy.accounts.removeAll { $0.name == name }
        return copy
    }
}
