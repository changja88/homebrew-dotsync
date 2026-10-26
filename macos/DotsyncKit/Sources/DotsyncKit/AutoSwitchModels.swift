import Foundation

/// Which account takes over when the one in use reaches a limit.
public enum AutoSwitchRule: String, Codable, Sendable {
    /// The lowest weekly use, so the accounts wear evenly.
    case even
    /// The soonest weekly reset, so less of a week's allowance goes unused.
    case weeklyReset = "weekly_reset"
}

/// Why auto switch is resting after a run that switched nothing.
public enum AutoSwitchRestReason: String, Codable, Sendable {
    case noCandidate = "no_candidate"
    case failed
    case belowLimit = "below_limit"
}

/// auto-switch.json: what the person chose, and what the last runs left for
/// the checker (when it last switched or refetched, how long it rests).
public struct AutoSwitchSettings: Codable, Equatable, Sendable {
    public var enabled: Bool
    public var rule: AutoSwitchRule
    /// Account names auto switch never picks.
    public var excluded: [String]
    public var lastSwitchAt: Date?
    public var lastRefetchAt: Date?
    public var restUntil: Date?
    public var restReason: AutoSwitchRestReason?

    public init(enabled: Bool, rule: AutoSwitchRule, excluded: [String], lastSwitchAt: Date? = nil,
                lastRefetchAt: Date? = nil, restUntil: Date? = nil, restReason: AutoSwitchRestReason? = nil) {
        self.enabled = enabled
        self.rule = rule
        self.excluded = excluded
        self.lastSwitchAt = lastSwitchAt
        self.lastRefetchAt = lastRefetchAt
        self.restUntil = restUntil
        self.restReason = restReason
    }

    public static let off = AutoSwitchSettings(enabled: false, rule: .even, excluded: [])
}

public enum AutoSwitchResult: String, Codable, Sendable {
    case switched
    case noCandidate = "no_candidate"
    case belowLimit = "below_limit"
    case unsavedLogin = "unsaved_login"
    case failed
}

/// One line of auto-switch-log.json.
public struct AutoSwitchLogEntry: Codable, Equatable, Sendable {
    public var at: Date
    /// The account in use, by name; nil on a login that isn't saved.
    public var from: String?
    /// The account switched to.
    public var to: String?
    /// The limit reached, as "5시간 92%".
    public var reason: String?
    public var rule: AutoSwitchRule
    public var result: AutoSwitchResult
    /// What went wrong, for `failed`.
    public var message: String?

    public init(at: Date, from: String?, to: String?, reason: String?, rule: AutoSwitchRule,
                result: AutoSwitchResult, message: String?) {
        self.at = at
        self.from = from
        self.to = to
        self.reason = reason
        self.rule = rule
        self.result = result
        self.message = message
    }
}

/// What the checker wakes the app for.
public enum AutoSwitchRequest: String, Sendable {
    /// The live numbers say the account in use reached a limit.
    case switchNow = "switch"
    /// The live numbers can't be matched to the account in use; probe again.
    case refetch
}
