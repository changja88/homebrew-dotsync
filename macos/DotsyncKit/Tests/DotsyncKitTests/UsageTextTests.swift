import Foundation
import Testing
@testable import DotsyncKit

let now = Date(timeIntervalSince1970: 1_791_115_200)  // 2026-10-04 12:00 UTC (Sun 21:00 KST)
let seoul = TimeZone(identifier: "Asia/Seoul")!

func after(days: Int = 0, hours: Int = 0, minutes: Int = 0, seconds: Int = 0) -> Date {
    now.addingTimeInterval(TimeInterval(((days * 24 + hours) * 60 + minutes) * 60 + seconds))
}

@Test func remainingSpellsHoursAndMinutes() {
    #expect(UsageText.remaining(until: after(hours: 3, minutes: 9), now: now) == "3시간 9분 후 초기화")
    #expect(UsageText.remaining(until: after(hours: 2), now: now) == "2시간 후 초기화")
    #expect(UsageText.remaining(until: after(minutes: 12), now: now) == "12분 후 초기화")
    #expect(UsageText.remaining(until: after(seconds: 30), now: now) == "1분 후 초기화")
}

@Test func remainingSpellsDaysAndHours() {
    #expect(UsageText.remaining(until: after(days: 4, hours: 23, minutes: 30), now: now) == "4일 23시간 후 초기화")
    #expect(UsageText.remaining(until: after(days: 1), now: now) == "1일 후 초기화")
}

@Test func remainingAfterTheResetOrWithoutOne() {
    #expect(UsageText.remaining(until: now, now: now) == "초기화됨")
    #expect(UsageText.remaining(until: after(minutes: -5), now: now) == "초기화됨")
    #expect(UsageText.remaining(until: nil, now: now) == "—")
}

@Test func fullWeekNamesTheResetDayInKorean() {
    let reset = Date(timeIntervalSince1970: 1_791_331_200)  // 2026-10-07 00:00 UTC = 09:00 KST, Wednesday
    #expect(UsageText.fullWeek(reset: reset, timeZone: seoul) == "10/7(수) 09:00 초기화")
    #expect(UsageText.resetDate(reset, timeZone: seoul) == "10/7(수) 09:00")
}

@Test func aLoginCountsDownInDaysThenHours() {
    #expect(UsageText.loginLeft(after(days: 16), now: now) == "16일 남음")
    #expect(UsageText.loginLeft(after(days: 1, hours: 21), now: now) == "2일 남음")  // as Claude's own warning rounds
    #expect(UsageText.loginLeft(after(days: 1), now: now) == "1일 남음")
    #expect(UsageText.loginLeft(after(hours: 4, minutes: 10), now: now) == "5시간 남음")
    #expect(UsageText.loginLeft(after(minutes: 5), now: now) == "1시간 남음")
}

@Test func aLoginPastItsDateIsExpiredAndNoDateIsADash() {
    #expect(UsageText.loginLeft(now, now: now) == "만료됨")
    #expect(UsageText.loginLeft(after(minutes: -5), now: now) == "만료됨")
    #expect(UsageText.loginLeft(nil, now: now) == "—")
    #expect(UsageText.loginExpired(after(minutes: -5), now: now))
    #expect(UsageText.loginExpired(now, now: now))
    #expect(!UsageText.loginExpired(after(minutes: 5), now: now))
    #expect(!UsageText.loginExpired(nil, now: now))
}

@Test func aLoginCountsAsExpiringFromThreeDaysBefore() {
    #expect(UsageText.loginExpiresSoon(after(days: 3), now: now))
    #expect(!UsageText.loginExpiresSoon(after(days: 3, seconds: 1), now: now))
    #expect(UsageText.loginExpiresSoon(after(hours: 5), now: now))
    #expect(UsageText.loginExpiresSoon(after(minutes: -5), now: now))
    #expect(!UsageText.loginExpiresSoon(nil, now: now))
}

@Test func aLoginsDateIsADashWithoutOne() {
    #expect(UsageText.loginDate(after(days: 2, hours: 12), timeZone: seoul) == "10/7(수) 09:00")
    #expect(UsageText.loginDate(nil, timeZone: seoul) == "—")
}

@Test func aWindowsResetDateIsADashWithoutOne() {
    #expect(UsageText.resetDate(of: UsageWindow(percent: 57, resetsAt: after(days: 2, hours: 12)), timeZone: seoul)
        == "10/7(수) 09:00")
    #expect(UsageText.resetDate(of: UsageWindow(percent: 57, resetsAt: nil), timeZone: seoul) == "—")
    #expect(UsageText.resetDate(of: nil, timeZone: seoul) == "—")
}

@Test func resetDateRoundsToTheNearestMinute() {
    // Claude reports resets like 02:59:59.85 UTC; people read that as 12:00 KST.
    let almostNoon = Date(timeIntervalSince1970: 1_791_082_799)  // 2026-10-04 02:59:59 UTC
    #expect(UsageText.resetDate(almostNoon, timeZone: seoul) == "10/4(일) 12:00")
    #expect(UsageText.fullWeek(reset: almostNoon, timeZone: seoul) == "10/4(일) 12:00 초기화")
}

@Test func agoCountsUpFromJustNow() {
    #expect(UsageText.ago(nil, now: now) == "갱신 전")
    #expect(UsageText.ago(after(seconds: -30), now: now) == "방금")
    #expect(UsageText.ago(after(minutes: -3), now: now) == "3분 전")
    #expect(UsageText.ago(after(hours: -2), now: now) == "2시간 전")
    #expect(UsageText.ago(after(hours: -26), now: now) == "1일 전")
}

@Test func theWidgetHeaderSaysRefreshingFailedOrWhenItFetched() {
    var file = UsageFile.empty
    #expect(UsageText.updateStatus(file, now: now) == .never)
    file.fetchedAt = after(minutes: -6)
    #expect(UsageText.updateStatus(file, now: now) == .fetched(after(minutes: -6)))
    file.refreshingSince = after(seconds: -5)
    #expect(UsageText.updateStatus(file, now: now) == .refreshing)
    // A mark left by an app that quit mid-refresh stops counting after two minutes.
    file.refreshingSince = after(minutes: -3)
    #expect(UsageText.updateStatus(file, now: now) == .fetched(after(minutes: -6)))
    file.lastError = "dotsync를 찾을 수 없어요"
    #expect(UsageText.updateStatus(file, now: now) == .failed)
    file.refreshingSince = after(seconds: -5)
    #expect(UsageText.updateStatus(file, now: now) == .refreshing)
}

@Test func theCountdownRunsUntilTheResetThenSaysItPassed() {
    #expect(UsageText.countdown(to: after(hours: 3), now: now) == .until(after(hours: 3)))
    #expect(UsageText.countdown(to: now, now: now) == .passed)
    #expect(UsageText.countdown(to: after(minutes: -1), now: now) == .passed)
    #expect(UsageText.countdown(to: nil, now: now) == .unknown)
}

@Test func fullOnlyFromNinetyEightPercentUntilTheReset() {
    #expect(UsageText.isFull(UsageWindow(percent: 98, resetsAt: after(days: 2)), now: now))
    #expect(!UsageText.isFull(UsageWindow(percent: 97, resetsAt: after(days: 2)), now: now))
    #expect(!UsageText.isFull(UsageWindow(percent: 99, resetsAt: after(minutes: -1)), now: now))
    #expect(UsageText.isFull(UsageWindow(percent: 100, resetsAt: nil), now: now))
    #expect(!UsageText.isFull(nil, now: now))
}

@Test func resetMeansTheResetTimePassed() {
    #expect(UsageText.isReset(UsageWindow(percent: 40, resetsAt: after(minutes: -1)), now: now))
    #expect(!UsageText.isReset(UsageWindow(percent: 40, resetsAt: after(minutes: 1)), now: now))
    #expect(!UsageText.isReset(UsageWindow(percent: 0, resetsAt: nil), now: now))
}

@Test func tiersSplitAtSixtyAndEightyFive() {
    #expect(UsageText.tier(59) == .low)
    #expect(UsageText.tier(60) == .mid)
    #expect(UsageText.tier(84) == .mid)
    #expect(UsageText.tier(85) == .high)
}

@Test func namesFollowDotsyncsRule() {
    #expect(AccountRules.isValidName("changja00"))
    #expect(AccountRules.isValidName("a.b-c_d"))
    #expect(!AccountRules.isValidName(""))
    #expect(!AccountRules.isValidName(".hidden"))
    #expect(!AccountRules.isValidName("a b"))
    #expect(!AccountRules.isValidName("밥"))
    #expect(!AccountRules.isValidName("default"))
    #expect(!AccountRules.isValidName("abc\n"))
}

@Test func labelsCountCodePointsAfterTrimming() {
    #expect(AccountRules.isValidLabel("  밥 업무용  "))
    #expect(AccountRules.isValidLabel(String(repeating: "x", count: 40)))
    #expect(!AccountRules.isValidLabel(String(repeating: "x", count: 41)))
    #expect(!AccountRules.isValidLabel("   "))
    #expect(!AccountRules.isValidLabel(""))
}

@Test func theInUseHeaderSaysWhenClaudeLastReportedTheNumbersItShows() {
    let week = after(days: 3)
    var file = UsageMerge.merge(previous: nil, report: report([account("a", week: week)], active: "a",
                                                              at: after(minutes: -58)))
    func live(at time: Date, week reset: Date = week) -> LiveUsage {
        LiveUsage(updatedAt: time, fiveHour: nil, sevenDay: UsageWindow(percent: 50, resetsAt: reset))
    }
    #expect(UsageText.updateStatus(file, live: live(at: after(seconds: -10)), now: now)
            == .fetched(after(seconds: -10)))
    // Another account's numbers, or older than the last refresh: the refresh's time.
    #expect(UsageText.updateStatus(file, live: live(at: after(seconds: -10), week: after(days: 5)), now: now)
            == .fetched(after(minutes: -58)))
    #expect(UsageText.updateStatus(file, live: live(at: after(hours: -2)), now: now)
            == .fetched(after(minutes: -58)))
    // A failed refresh leaves the live numbers shown, and they are fresh.
    file.lastError = "dotsync를 찾을 수 없어요"
    #expect(UsageText.updateStatus(file, live: live(at: after(seconds: -10)), now: now)
            == .fetched(after(seconds: -10)))
    file.refreshingSince = after(seconds: -5)
    #expect(UsageText.updateStatus(file, live: live(at: after(seconds: -10)), now: now) == .refreshing)
}
