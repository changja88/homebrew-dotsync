import Foundation
import Testing
@testable import DotsyncKit

func file(_ accounts: [AccountUsage], active: String?) -> UsageFile {
    var file = UsageFile.empty
    file.accounts = accounts
    file.active = active
    return file
}

@Test func theAccountInUseGoesOnTopNotInTheList() {
    let layout = WidgetLayout(
        file: file([account("a", week: after(days: 2)), account("b", week: after(days: 1))], active: "a"), now: now)
    #expect(layout.current?.name == "a")
    #expect(layout.rows.map(\.name) == ["b"])
    #expect(layout.hidden == 0)
    #expect(!layout.switchNeedsConfirmation)
}

@Test func extraAccountsCollapseIntoACount() {
    let accounts = (1...6).map { account("x\($0)", week: after(days: $0)) }
    let layout = WidgetLayout(file: file(accounts, active: "x1"), now: now, maxRows: 4)
    #expect(layout.rows.map(\.name) == ["x2", "x3", "x4", "x5"])
    #expect(layout.hidden == 1)
}

@Test func theLongWidgetsShowSevenOtherAccounts() {
    let accounts = (1...9).map { account("x\($0)", week: after(days: $0)) }
    let layout = WidgetLayout(file: file(accounts, active: "x1"), now: now, maxRows: WidgetLayout.extraLargeRows)
    #expect(layout.rows.map(\.name) == ["x2", "x3", "x4", "x5", "x6", "x7", "x8"])
    #expect(layout.hidden == 1)
}

/// The list-only widget: "외 n개" sits under the rows, so it costs one.
@Test func sevenOthersAllShowWhereTheMoreLineSharesTheRows() {
    let accounts = (1...8).map { account("x\($0)", week: after(days: $0)) }
    let layout = WidgetLayout(file: file(accounts, active: "x1"), now: now,
                              maxRows: WidgetLayout.extraLargeRows, moreTakesARow: true)
    #expect(layout.rows.map(\.name) == ["x2", "x3", "x4", "x5", "x6", "x7", "x8"])
    #expect(layout.hidden == 0)
}

@Test func theMoreLineTakesTheLastRowsPlace() {
    let accounts = (1...9).map { account("x\($0)", week: after(days: $0)) }
    let layout = WidgetLayout(file: file(accounts, active: "x1"), now: now,
                              maxRows: WidgetLayout.extraLargeRows, moreTakesARow: true)
    #expect(layout.rows.map(\.name) == ["x2", "x3", "x4", "x5", "x6", "x7"])
    #expect(layout.hidden == 2)
}

@Test func anUnsavedLoginInUseAsksBeforeSwitching() {
    var unsaved = file([account("a", week: after(days: 1))], active: nil)
    unsaved.unsavedSeat = UnsavedSeat(email: "u@x", status: .ok)
    let layout = WidgetLayout(file: unsaved, now: now)
    #expect(layout.current == nil)
    #expect(layout.unsavedSeat?.email == "u@x")
    #expect(layout.rows.map(\.name) == ["a"])
    #expect(layout.switchNeedsConfirmation)
}

@Test func useButtonsHideWhileAutoSwitchIsOn() {
    let accounts = file([account("a"), account("b")], active: "a")
    #expect(WidgetLayout(file: accounts, now: now).showsUseButtons)
    #expect(!WidgetLayout(file: accounts, now: now, autoSwitchOn: true).showsUseButtons)
}

@Test func theEmptyFileShowsNothing() {
    let layout = WidgetLayout(file: .empty, now: now)
    #expect(layout.current == nil && layout.unsavedSeat == nil && layout.rows.isEmpty && layout.hidden == 0)
}

@Test func rowKinds() {
    var full = account("f", week: after(days: 3))
    full.sevenDay = UsageWindow(percent: 99, resetsAt: after(days: 3))
    #expect(WidgetLayout.kind(of: full, now: now) == .fullWeek(after(days: 3)))
    #expect(WidgetLayout.kind(of: account("l", .loginRequired), now: now) == .loginLost)
    #expect(WidgetLayout.kind(of: account("m", week: after(days: 1)), now: now) == .metrics)
}

@Test func aFullWeekGoesBackToMetricsOnceItResets() {
    var full = account("f")
    full.sevenDay = UsageWindow(percent: 100, resetsAt: after(minutes: -1))
    #expect(WidgetLayout.kind(of: full, now: now) == .metrics)
}

private func expiring(_ date: Date?) -> AccountUsage {
    var usage = account("a")
    usage.fiveHour = nil  // their resets would add entries of their own
    usage.sevenDay = nil
    usage.loginExpiresAt = date
    return usage
}

@Test func theTimelineChangesWhereALoginsWarningDoes() {
    // Three days before, each day after that, and the expiry: within the day ahead.
    let starting = WidgetLayout.timelineDates(for: file([expiring(after(days: 3, hours: 5))], active: "a"), now: now)
    #expect(starting == [now, after(hours: 5)])
    let ending = WidgetLayout.timelineDates(for: file([expiring(after(hours: 20))], active: "a"), now: now)
    #expect(ending == [now, after(hours: 20)])
    let daily = WidgetLayout.timelineDates(for: file([expiring(after(days: 1, hours: 2))], active: "a"), now: now)
    #expect(daily == [now, after(hours: 2)])
}

@Test func aLoginFarFromItsExpiryAddsNoEntry() {
    #expect(WidgetLayout.timelineDates(for: file([expiring(after(days: 20))], active: "a"), now: now) == [now])
    #expect(WidgetLayout.timelineDates(for: file([expiring(after(days: -1))], active: "a"), now: now) == [now])
}

@Test func theListWidgetReloadsWhenALoginsHoursChange() {
    func next(_ date: Date?) -> Date {
        WidgetLayout.nextReload(after: now, showsInUse: false, file: file([expiring(date)], active: "a"))
    }
    #expect(next(nil) == after(days: 1))
    #expect(next(after(days: 20)) == after(days: 1))
    #expect(next(after(hours: 5)) == after(hours: 1))  // "5시간 남음" counts down
    #expect(next(after(hours: 5, minutes: 30)) == after(minutes: 30))  // "6시간" turns "5시간" then
    #expect(next(after(days: 1, hours: 2)) == after(hours: 2))  // when hours begin
    #expect(next(after(minutes: -5)) == after(days: 1))  // over: nothing counts anymore
    #expect(WidgetLayout.nextReload(after: now, showsInUse: true, file: file([expiring(after(hours: 5))], active: "a"))
        == after(minutes: 15))
}

@Test func theTimelineHasAnEntryOnlyWhereTheWidgetChanges() {
    var accounts = [account("a", week: after(days: 2)), account("b", week: after(hours: 30))]
    accounts[1].fiveHour = UsageWindow(percent: 50, resetsAt: after(hours: 4))
    let dates = WidgetLayout.timelineDates(for: file(accounts, active: "a"), now: now)
    // Both five-hour resets; the weekly ones are more than a day away.
    #expect(dates == [now, after(hours: 2), after(hours: 4)])
}

@Test func passedAndSharedResetsAddNoEntries() {
    var accounts = [account("a", week: after(days: -1)), account("b", week: after(hours: 2))]
    accounts[0].fiveHour = UsageWindow(percent: 50, resetsAt: after(minutes: -5))
    let dates = WidgetLayout.timelineDates(for: file(accounts, active: nil), now: now)
    #expect(dates == [now, after(hours: 2)])
}

@Test func theUnsavedLoginsResetsCount() {
    var unsaved = UsageFile.empty
    unsaved.unsavedSeat = UnsavedSeat(email: "u@x", status: .ok,
                                      fiveHour: UsageWindow(percent: 5, resetsAt: after(hours: 3)))
    #expect(WidgetLayout.timelineDates(for: unsaved, now: now) == [now, after(hours: 3)])
}

@Test func 조회중EndsWithItsOwnEntryWhenTheAppLeftItBehind() {
    var refreshing = UsageFile.empty
    refreshing.refreshingSince = after(seconds: -30)
    #expect(WidgetLayout.timelineDates(for: refreshing, now: now) == [now, after(seconds: 90)])
}

@Test func widgetsWithTheInUseCardReloadEveryFifteenMinutes() {
    #expect(WidgetLayout.nextReload(after: now, showsInUse: true) == after(minutes: 15))
    // The other accounts change only at their resets, which have entries.
    #expect(WidgetLayout.nextReload(after: now, showsInUse: false) == after(days: 1))
}
