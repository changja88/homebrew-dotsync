import Foundation
import Testing
@testable import DotsyncKit

func account(_ name: String, _ status: AccountStatus = .ok, week: Date? = nil,
             five: Int = 10, fetchedAt: Date? = nil, error: String? = nil) -> AccountUsage {
    AccountUsage(name: name, label: name, email: "\(name)@x", status: status, error: error,
                 fiveHour: status == .loginRequired ? nil : UsageWindow(percent: five, resetsAt: after(hours: 2)),
                 sevenDay: status == .loginRequired ? nil : UsageWindow(percent: 20, resetsAt: week),
                 fetchedAt: fetchedAt)
}

func report(_ accounts: [AccountUsage], active: String? = nil, at: Date = now) -> UsageReport {
    UsageReport(fetchedAt: at, active: active, unsavedSeat: nil, accounts: accounts)
}

@Test func sortsByWeeklyResetSoonestFirst() {
    let sorted = UsageOrder.sorted([
        account("c", week: after(days: 3)),
        account("lost", .loginRequired),
        account("a", week: after(days: 1)),
        account("unread", week: nil),
        account("b", week: after(days: 2)),
    ])
    #expect(sorted.map(\.name) == ["a", "b", "c", "lost", "unread"])
}

@Test func sortsEqualResetsByLabel() {
    let sorted = UsageOrder.sorted([account("y", week: after(days: 1)), account("x", week: after(days: 1))])
    #expect(sorted.map(\.name) == ["x", "y"])
}

@Test func mergeTakesFreshValuesAndStampsThem() {
    let merged = UsageMerge.merge(previous: nil, report: report([account("a", five: 31)], active: "a"))
    #expect(merged.active == "a")
    #expect(merged.fetchedAt == now)
    #expect(merged.lastError == nil)
    #expect(merged.accounts[0].fiveHour?.percent == 31)
    #expect(merged.accounts[0].fetchedAt == now)
}

@Test func mergeKeepsTheLastGoodValuesOfAFailedProbe() {
    let earlier = after(minutes: -10)
    var previous = UsageFile.empty
    previous.accounts = [account("a", five: 44, fetchedAt: earlier)]
    let failed = AccountUsage(name: "a", label: "A2", email: "a@x", status: .error, error: "timeout")

    let merged = UsageMerge.merge(previous: previous, report: report([failed]))

    let a = merged.accounts[0]
    #expect(a.status == .error)
    #expect(a.error == "timeout")
    #expect(a.label == "A2")
    #expect(a.fiveHour?.percent == 44)
    #expect(a.fetchedAt == earlier)
}

@Test func mergeOfAFailedProbeWithoutHistoryHasNoValues() {
    let failed = AccountUsage(name: "a", label: "a", email: nil, status: .error, error: "timeout")
    let merged = UsageMerge.merge(previous: .empty, report: report([failed]))
    #expect(merged.accounts[0].fiveHour == nil)
    #expect(merged.accounts[0].fetchedAt == nil)
}

@Test func mergeClearsALostLogin() {
    var previous = UsageFile.empty
    previous.accounts = [account("a", fetchedAt: after(minutes: -10))]
    let merged = UsageMerge.merge(previous: previous, report: report([account("a", .loginRequired)]))
    #expect(merged.accounts[0].fiveHour == nil)
    #expect(merged.accounts[0].fetchedAt == nil)
}

@Test func mergeDropsAccountsRemovedElsewhereAndClearsTheError() {
    var previous = UsageFile.empty
    previous.lastError = "old"
    previous.accounts = [account("gone"), account("a")]
    let merged = UsageMerge.merge(previous: previous, report: report([account("a")]))
    #expect(merged.accounts.map(\.name) == ["a"])
    #expect(merged.lastError == nil)
}

@Test func failedKeepsWhatWasShownAndSaysWhy() {
    var previous = UsageFile.empty
    previous.fetchedAt = after(minutes: -5)
    previous.accounts = [account("a")]
    let file = UsageMerge.failed(previous: previous, message: "dotsync를 찾을 수 없어요")
    #expect(file.accounts == previous.accounts)
    #expect(file.fetchedAt == previous.fetchedAt)
    #expect(file.lastError == "dotsync를 찾을 수 없어요")
    #expect(UsageMerge.failed(previous: nil, message: "x").accounts.isEmpty)
}

@Test func switchedMovesTheActiveAccount() {
    var file = UsageFile.empty
    file.active = "a"
    file.unsavedSeat = UnsavedSeat(email: "u@x", status: .ok)
    let switched = UsageMerge.switched(file, to: "b")
    #expect(switched.active == "b")
    #expect(switched.unsavedSeat == nil)
}

@Test func renamedAndRemovedEditOneAccount() {
    var file = UsageFile.empty
    file.accounts = [account("a"), account("b")]
    let renamed = UsageMerge.renamed(file, AccountInfo(name: "b", label: "밥", email: nil, loggedIn: true))
    #expect(renamed.accounts.map(\.label) == ["a", "밥"])
    #expect(UsageMerge.removed(file, "a").accounts.map(\.name) == ["b"])
}

@Test func refreshingMarksTheFileUntilTheRefreshEnds() {
    var file = UsageFile.empty
    file.active = "a"
    let marked = UsageMerge.refreshing(file, since: now)
    #expect(marked.refreshingSince == now)
    #expect(marked.active == "a")
    #expect(UsageMerge.merge(previous: marked, report: report([account("a")], active: "a")).refreshingSince == nil)
    #expect(UsageMerge.failed(previous: marked, message: "x").refreshingSince == nil)
}
