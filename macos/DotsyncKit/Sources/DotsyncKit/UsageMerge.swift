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

    /// `file` with the active account's windows from Claude's status line, when
    /// those came after its last probe. The snapshot names no account: right
    /// after a switch Claude may still hand over the previous account's numbers,
    /// so they count as the active one's only when their weekly reset is the one
    /// its probe saw. Only for drawing; usage.json keeps what dotsync read.
    public static func withLive(_ file: UsageFile, _ live: LiveUsage?) -> UsageFile {
        guard let live,
              let index = file.accounts.firstIndex(where: { $0.name == file.active }),
              file.accounts[index].status == .ok,
              live.updatedAt > file.accounts[index].fetchedAt ?? .distantPast,
              let liveWeek = live.sevenDay?.resetsAt,
              let probedWeek = file.accounts[index].sevenDay?.resetsAt,
              abs(liveWeek.timeIntervalSince(probedWeek)) <= sameResetSlack
        else { return file }
        var copy = file
        if let window = live.fiveHour { copy.accounts[index].fiveHour = window }
        if let window = live.sevenDay { copy.accounts[index].sevenDay = window }
        copy.accounts[index].fetchedAt = live.updatedAt
        return copy
    }

    /// Claude says 09:00:00 where dotsync's probe says 08:59:59.
    static let sameResetSlack: TimeInterval = 60

    /// The whole refresh failed: keep what was shown and say why.
    public static func failed(previous: UsageFile?, message: String) -> UsageFile {
        var file = previous ?? .empty
        file.lastError = message
        file.refreshingSince = nil
        return file
    }

    /// A refresh started; what was shown stays until it ends.
    public static func refreshing(_ file: UsageFile?, since start: Date) -> UsageFile {
        var copy = file ?? .empty
        copy.refreshingSince = start
        return copy
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
