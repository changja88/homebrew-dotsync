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
