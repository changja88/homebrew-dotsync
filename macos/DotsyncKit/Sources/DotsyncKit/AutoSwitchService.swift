import Foundation

/// What the app does when the checker wakes it (spec 5.3): probe every
/// account, and if the probe says the one in use reached a limit, switch to
/// the one the rule picks. The decision is made again on the probe alone —
/// the live numbers that woke the app may be another account's or old.
public struct AutoSwitchService: Sendable {
    public let accounts: AccountService
    public let store: AutoSwitchStore
    let gate: RefreshGate

    public init(accounts: AccountService, store: AutoSwitchStore, gate: RefreshGate = .shared) {
        self.accounts = accounts
        self.store = store
        self.gate = gate
    }

    /// The entry written to the log, or nil when there is nothing to note:
    /// auto switch is off or resting, or a refetch found the account in use
    /// below its limits. The checker never wakes the app in a rest; a link
    /// opened some other way waits it out too.
    public func run(_ request: AutoSwitchRequest, now: Date = Date()) async -> AutoSwitchLogEntry? {
        let settings = store.loadSettings()
        guard settings.enabled, !(settings.restUntil.map { $0 > now } ?? false) else { return nil }
        guard let outcome = await outcome(of: request, now: now) else { return nil }
        // Only the fields a run owns: the person may have changed the rest
        // while dotsync worked.
        try? store.updateSettings { settings in
            if request == .refetch { settings.lastRefetchAt = now }
            if outcome.switched {
                settings.lastSwitchAt = now
                settings.restUntil = nil
                settings.restReason = nil
            }
            if let rest = outcome.rest {
                settings.restUntil = now.addingTimeInterval(rest.length)
                settings.restReason = rest.reason
            }
        }
        if let entry = outcome.entry { try? store.append(entry) }
        return outcome.entry
    }

    /// dotsync can't be found: rest and say so, or every new live number
    /// would wake the app again.
    public static func recordUnavailable(_ store: AutoSwitchStore, message: String,
                                         now: Date = Date()) -> AutoSwitchLogEntry {
        let entry = AutoSwitchLogEntry(at: now, from: nil, to: nil, reason: nil, rule: store.loadSettings().rule,
                                       result: .failed, message: message)
        try? store.updateSettings { settings in
            settings.restUntil = now.addingTimeInterval(AutoSwitchRules.restAfterFailure)
            settings.restReason = .failed
        }
        try? store.append(entry)
        return entry
    }

    private struct Outcome {
        var entry: AutoSwitchLogEntry?
        var rest: (length: TimeInterval, reason: AutoSwitchRestReason)?
        var switched = false
    }

    /// nil when auto switch was turned off during the probe.
    private func outcome(of request: AutoSwitchRequest, now: Date) async -> Outcome? {
        let before = accounts.current().fetchedAt
        let file = await gate.refresh(accounts)
        let settings = store.loadSettings()
        guard settings.enabled else { return nil }
        func entry(_ result: AutoSwitchResult, to: String? = nil, reason: String? = nil,
                   message: String? = nil) -> AutoSwitchLogEntry {
            AutoSwitchLogEntry(at: now, from: file.activeAccount?.name, to: to, reason: reason, rule: settings.rule,
                               result: result, message: message)
        }

        // A failed probe keeps the old numbers in usage.json.
        if file.lastError != nil || file.fetchedAt == before {
            return Outcome(entry: entry(.failed, message: file.lastError ?? "조회하지 못했어요"),
                           rest: (AutoSwitchRules.restAfterFailure, .failed))
        }
        guard let active = file.activeAccount, file.unsavedSeat == nil else {
            return Outcome(entry: request == .switchNow ? entry(.unsavedLogin) : nil)
        }
        // So does one that failed only for the account in use (merge keeps
        // its last good numbers): nothing new was read about it.
        guard active.status == .ok else {
            let message = active.error ?? (active.status == .loginRequired
                ? "사용 중 계정의 로그인이 풀렸어요" : "사용 중 계정을 조회하지 못했어요")
            return Outcome(entry: entry(.failed, message: message), rest: (AutoSwitchRules.restAfterFailure, .failed))
        }
        guard let trigger = AutoSwitchRules.trigger(for: active, now: now) else {
            guard request == .switchNow else { return Outcome() }
            return Outcome(entry: entry(.belowLimit), rest: (AutoSwitchRules.restAfterBelowLimit, .belowLimit))
        }
        guard let next = AutoSwitchRules.pick(from: file, settings: settings, now: now) else {
            return Outcome(entry: entry(.noCandidate, reason: trigger.text),
                           rest: (AutoSwitchRules.restAfterNoCandidate, .noCandidate))
        }
        switch await accounts.use(next.name) {
        case .switched:
            return Outcome(entry: entry(.switched, to: next.name, reason: trigger.text), switched: true)
        case .needsConfirmation:
            // Claude's login changed since the probe; it isn't saved, so it stays.
            return Outcome(entry: entry(.unsavedLogin, reason: trigger.text))
        case .failed(let error):
            return Outcome(entry: entry(.failed, reason: trigger.text, message: error.message),
                           rest: (AutoSwitchRules.restAfterFailure, .failed))
        }
    }
}
