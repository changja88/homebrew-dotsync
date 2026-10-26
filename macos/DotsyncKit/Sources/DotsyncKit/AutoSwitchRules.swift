import Foundation

/// The limit the account in use reached.
public struct AutoSwitchTrigger: Equatable, Sendable {
    public enum Window: Equatable, Sendable {
        case fiveHour
        case sevenDay
    }

    public var window: Window
    public var percent: Int

    public init(window: Window, percent: Int) {
        self.window = window
        self.percent = percent
    }

    /// "5시간 92%", "주간 98%".
    public var text: String {
        switch window {
        case .fiveHour: "5시간 \(percent)%"
        case .sevenDay: "주간 \(percent)%"
        }
    }
}

/// What the checker does about the numbers it just read.
public enum AutoSwitchDecision: Equatable, Sendable {
    case none
    case switchNow(AutoSwitchTrigger)
    /// The live numbers can't be matched to the account in use (its week
    /// rolled over, or Claude logged in elsewhere): probe every account.
    case refetch
}

/// When auto switch acts and which account takes over (spec 5.2, 5.3).
/// The limits are the person's and fixed: no screen changes them.
public enum AutoSwitchRules {
    public static let fiveHourLimit = 90
    public static let weeklyLimit = 98
    public static let restAfterNoCandidate: TimeInterval = 15 * 60
    public static let restAfterFailure: TimeInterval = 15 * 60
    public static let restAfterBelowLimit: TimeInterval = 5 * 60
    public static let refetchInterval: TimeInterval = 15 * 60
    /// A session answering through the switch hands over the previous
    /// account's numbers once or twice; that is no reason to refetch.
    public static let quietAfterSwitch: TimeInterval = 5 * 60

    /// A window's use now: none once it has reset, and none when unread.
    public static func percent(_ window: UsageWindow?, now: Date) -> Int {
        guard let window else { return 0 }
        if let reset = window.resetsAt, reset <= now { return 0 }
        return window.percent
    }

    /// The limit `account` reached, the five-hour one first; nil below both.
    public static func trigger(for account: AccountUsage, now: Date) -> AutoSwitchTrigger? {
        let fiveHour = percent(account.fiveHour, now: now)
        if fiveHour >= fiveHourLimit { return AutoSwitchTrigger(window: .fiveHour, percent: fiveHour) }
        let sevenDay = percent(account.sevenDay, now: now)
        if sevenDay >= weeklyLimit { return AutoSwitchTrigger(window: .sevenDay, percent: sevenDay) }
        return nil
    }

    /// Only numbers newer than the last probe count: the app's own writes to
    /// the folder wake the checker as well, and must not start a run again.
    public static func decide(_ settings: AutoSwitchSettings, file: UsageFile, live: LiveUsage?,
                              now: Date) -> AutoSwitchDecision {
        guard settings.enabled, file.unsavedSeat == nil, file.activeAccount != nil, let live else { return .none }
        if let probed = file.fetchedAt, live.updatedAt <= probed { return .none }
        if let rest = settings.restUntil, rest > now { return .none }
        if UsageMerge.liveMatchesActive(file, live) {
            guard let active = UsageMerge.withLive(file, live).activeAccount,
                  let trigger = trigger(for: active, now: now) else { return .none }
            return .switchNow(trigger)
        }
        if let switched = settings.lastSwitchAt, now.timeIntervalSince(switched) < quietAfterSwitch { return .none }
        if let refetched = settings.lastRefetchAt, now.timeIntervalSince(refetched) < refetchInterval {
            return .none
        }
        return .refetch
    }

    /// The account to switch to, from a fresh probe: any saved account but
    /// the one in use and the excluded, that reads and is below both limits.
    public static func pick(from file: UsageFile, settings: AutoSwitchSettings, now: Date) -> AccountUsage? {
        let candidates = file.accounts.filter { account in
            account.name != file.active && !settings.excluded.contains(account.name) && account.status == .ok
                && percent(account.fiveHour, now: now) < fiveHourLimit
                && percent(account.sevenDay, now: now) < weeklyLimit
        }
        switch settings.rule {
        case .even:
            return candidates.min { one, other in
                (percent(one.sevenDay, now: now), percent(one.fiveHour, now: now), one.name)
                    < (percent(other.sevenDay, now: now), percent(other.fiveHour, now: now), other.name)
            }
        case .weeklyReset:
            return candidates.min { one, other in
                (upcomingWeekReset(one, now: now), percent(one.sevenDay, now: now), one.name)
                    < (upcomingWeekReset(other, now: now), percent(other.sevenDay, now: now), other.name)
            }
        }
    }

    /// The weekly reset still ahead. One that passed starts a week dotsync
    /// has not read the end of, so it sorts last with the unknown.
    private static func upcomingWeekReset(_ account: AccountUsage, now: Date) -> Date {
        guard let reset = account.sevenDay?.resetsAt, reset > now else { return .distantFuture }
        return reset
    }
}
