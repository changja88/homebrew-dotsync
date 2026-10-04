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

@Test func remainingShortDropsTheSuffix() {
    #expect(UsageText.remainingShort(until: after(hours: 1, minutes: 20), now: now) == "1시간 20분")
    #expect(UsageText.remainingShort(until: after(days: 1, hours: 8), now: now) == "1일 8시간")
    #expect(UsageText.remainingShort(until: after(minutes: -1), now: now) == "초기화됨")
    #expect(UsageText.remainingShort(until: nil, now: now) == "—")
}

@Test func fullWeekNamesTheResetDayInKorean() {
    let reset = Date(timeIntervalSince1970: 1_791_331_200)  // 2026-10-07 00:00 UTC = 09:00 KST, Wednesday
    #expect(UsageText.fullWeek(reset: reset, timeZone: seoul) == "10/7(수) 09:00 초기화")
    #expect(UsageText.resetDate(reset, timeZone: seoul) == "10/7(수) 09:00")
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
