import Foundation
import Testing
@testable import DotsyncKit

private let t0 = Date(timeIntervalSince1970: 1_793_016_000)  // 2026-10-26 12:00 UTC
private let weekReset = t0.addingTimeInterval(3 * 86_400)
private let fiveReset = t0.addingTimeInterval(3_600)
private let probedAt = t0.addingTimeInterval(-120)
private let on = AutoSwitchSettings(enabled: true, rule: .even, excluded: [])

private func seat(_ name: String, five: Int = 10, week: Int = 20, fiveResets: Date? = fiveReset,
                  weekResets: Date? = weekReset, status: AccountStatus = .ok) -> AccountUsage {
    AccountUsage(name: name, label: name, email: nil, status: status, error: nil,
                 fiveHour: UsageWindow(percent: five, resetsAt: fiveResets),
                 sevenDay: UsageWindow(percent: week, resetsAt: weekResets), fetchedAt: probedAt)
}

private func usageFile(_ accounts: [AccountUsage], active: String? = "a",
                       fetchedAt: Date? = probedAt) -> UsageFile {
    UsageFile(version: 1, fetchedAt: fetchedAt, lastError: nil, active: active, unsavedSeat: nil,
              accounts: accounts)
}

private func snapshot(at: Date = t0.addingTimeInterval(-10), five: Int = 10, week: Int = 20,
                      fiveResets: Date? = fiveReset, weekResets: Date? = weekReset) -> LiveUsage {
    LiveUsage(updatedAt: at, fiveHour: UsageWindow(percent: five, resetsAt: fiveResets),
              sevenDay: UsageWindow(percent: week, resetsAt: weekResets))
}

private func settings(_ change: (inout AutoSwitchSettings) -> Void) -> AutoSwitchSettings {
    var copy = on
    change(&copy)
    return copy
}

private let twoAccounts = usageFile([seat("a", five: 50), seat("b")])

// MARK: decide

@Test func decideSwitchesWhenTheLiveFiveHourReachesNinety() {
    let decision = AutoSwitchRules.decide(on, file: twoAccounts, live: snapshot(five: 90), now: t0)
    #expect(decision == .switchNow(AutoSwitchTrigger(window: .fiveHour, percent: 90)))
    #expect(AutoSwitchTrigger(window: .fiveHour, percent: 90).text == "5시간 90%")
}

@Test func decideSwitchesWhenTheWeekReachesNinetyEight() {
    let decision = AutoSwitchRules.decide(on, file: twoAccounts, live: snapshot(week: 98), now: t0)
    #expect(decision == .switchNow(AutoSwitchTrigger(window: .sevenDay, percent: 98)))
    #expect(AutoSwitchTrigger(window: .sevenDay, percent: 98).text == "주간 98%")
}

@Test func decideWaitsBelowTheLimits() {
    #expect(AutoSwitchRules.decide(on, file: twoAccounts, live: snapshot(five: 89, week: 97), now: t0) == .none)
}

@Test func decideIgnoresLiveNotNewerThanTheLastProbe() {
    // The app's own writes wake the checker too; they bring no new numbers.
    let live = snapshot(at: probedAt, five: 95)
    #expect(AutoSwitchRules.decide(on, file: twoAccounts, live: live, now: t0) == .none)
}

@Test func decideTreatsNoProbeTimeAsOlderThanLive() {
    let file = usageFile([seat("a"), seat("b")], fetchedAt: nil)
    let live = snapshot(weekResets: weekReset.addingTimeInterval(3_600))
    #expect(AutoSwitchRules.decide(on, file: file, live: live, now: t0) == .refetch)
}

@Test func decideDoesNothingWhenOff() {
    #expect(AutoSwitchRules.decide(.off, file: twoAccounts, live: snapshot(five: 95), now: t0) == .none)
}

@Test func decideDoesNothingOnAnUnsavedLogin() {
    var file = usageFile([seat("a"), seat("b")], active: nil)
    file.unsavedSeat = UnsavedSeat(email: "new@x", status: .ok, error: nil, fiveHour: nil, sevenDay: nil)
    #expect(AutoSwitchRules.decide(on, file: file, live: snapshot(five: 95), now: t0) == .none)
}

@Test func decideRestsUntilTheRestEnds() {
    let live = snapshot(five: 95)
    let resting = settings { $0.restUntil = t0.addingTimeInterval(60) }
    let rested = settings { $0.restUntil = t0.addingTimeInterval(-1) }
    #expect(AutoSwitchRules.decide(resting, file: twoAccounts, live: live, now: t0) == .none)
    #expect(AutoSwitchRules.decide(rested, file: twoAccounts, live: live, now: t0)
        == .switchNow(AutoSwitchTrigger(window: .fiveHour, percent: 95)))
}

@Test func decideTreatsAPassedWindowAsEmpty() {
    let passed = t0.addingTimeInterval(-60)
    let file = usageFile([seat("a", five: 95, fiveResets: passed), seat("b")])
    let live = snapshot(five: 95, fiveResets: passed)
    #expect(AutoSwitchRules.decide(on, file: file, live: live, now: t0) == .none)
}

@Test func decideSwitchesAwayFromAnExcludedActiveAccount() {
    let excludingA = settings { $0.excluded = ["a"] }
    #expect(AutoSwitchRules.decide(excludingA, file: twoAccounts, live: snapshot(five: 95), now: t0)
        == .switchNow(AutoSwitchTrigger(window: .fiveHour, percent: 95)))
}

@Test func decideAsksForARefetchWhenLiveDoesNotMatch() {
    // Another week, or another account: Claude logged in elsewhere.
    let live = snapshot(five: 95, weekResets: weekReset.addingTimeInterval(3_600))
    #expect(AutoSwitchRules.decide(on, file: twoAccounts, live: live, now: t0) == .refetch)
}

@Test func decideDoesNotRefetchWithinFifteenMinutesOfTheLast() {
    let live = snapshot(weekResets: weekReset.addingTimeInterval(3_600))
    let recent = settings { $0.lastRefetchAt = t0.addingTimeInterval(-899) }
    let older = settings { $0.lastRefetchAt = t0.addingTimeInterval(-901) }
    #expect(AutoSwitchRules.decide(recent, file: twoAccounts, live: live, now: t0) == .none)
    #expect(AutoSwitchRules.decide(older, file: twoAccounts, live: live, now: t0) == .refetch)
}

@Test func decideDoesNotRefetchWithinFiveMinutesOfASwitch() {
    // Right after a switch, a session still answering hands over the
    // previous account's numbers once or twice.
    let live = snapshot(weekResets: weekReset.addingTimeInterval(3_600))
    let justSwitched = settings { $0.lastSwitchAt = t0.addingTimeInterval(-299) }
    let switchedBefore = settings { $0.lastSwitchAt = t0.addingTimeInterval(-301) }
    #expect(AutoSwitchRules.decide(justSwitched, file: twoAccounts, live: live, now: t0) == .none)
    #expect(AutoSwitchRules.decide(switchedBefore, file: twoAccounts, live: live, now: t0) == .refetch)
}

// MARK: pick

@Test func pickEvenTakesTheLowestWeekThenFiveHourThenName() {
    let file = usageFile([seat("a", five: 95), seat("b", five: 10, week: 30), seat("c", five: 50, week: 10),
                          seat("e", five: 20, week: 10), seat("d", five: 20, week: 10)])
    #expect(AutoSwitchRules.pick(from: file, settings: on, now: t0)?.name == "d")
}

@Test func pickWeeklyResetTakesTheSoonestResetAndPutsUnknownLast() {
    let byReset = settings { $0.rule = .weeklyReset }
    let soonest = t0.addingTimeInterval(86_400)
    let first = usageFile([seat("a", five: 95), seat("b", weekResets: t0.addingTimeInterval(2 * 86_400)),
                           seat("c", weekResets: soonest), seat("d", weekResets: nil)])
    #expect(AutoSwitchRules.pick(from: first, settings: byReset, now: t0)?.name == "c")

    let tie = usageFile([seat("a", five: 95), seat("c", week: 30, weekResets: soonest),
                         seat("e", week: 10, weekResets: soonest)])
    #expect(AutoSwitchRules.pick(from: tie, settings: byReset, now: t0)?.name == "e")
}

@Test func pickWeeklyResetPutsAPassedResetLast() {
    // Its next reset is a week away, and dotsync has not read it yet.
    let byReset = settings { $0.rule = .weeklyReset }
    let file = usageFile([seat("a", five: 95), seat("b", week: 0, weekResets: t0.addingTimeInterval(-60)),
                          seat("c", weekResets: t0.addingTimeInterval(6 * 86_400))])
    #expect(AutoSwitchRules.pick(from: file, settings: byReset, now: t0)?.name == "c")
}

@Test func pickSkipsActiveExcludedUnhealthyAndFullAccounts() {
    let excludingB = settings { $0.excluded = ["b"] }
    let file = usageFile([seat("a"), seat("b"), seat("c", status: .loginRequired), seat("d", five: 90),
                          seat("e", week: 98)])
    #expect(AutoSwitchRules.pick(from: file, settings: excludingB, now: t0) == nil)
}

@Test func pickCountsAPassedWindowAsEmpty() {
    let file = usageFile([seat("a", five: 95), seat("b", week: 99, weekResets: t0.addingTimeInterval(-60))])
    #expect(AutoSwitchRules.pick(from: file, settings: on, now: t0)?.name == "b")
}
