import Foundation
import Testing
@testable import DotsyncKit

/// What claude-hud wrote on 2026-10-26 (display.externalUsageWritePath).
let liveSample = """
{
  "updated_at": "2026-10-26T08:55:36.440Z",
  "five_hour": {
    "used_percentage": 60,
    "resets_at": "2026-10-26T12:20:00.000Z"
  },
  "seven_day": {
    "used_percentage": 93,
    "resets_at": "2026-10-30T23:00:00.000Z"
  }
}
"""

@Test func liveUsageReadsClaudeHudsSnapshot() throws {
    let live = try #require(LiveUsage(json: Data(liveSample.utf8)))
    #expect(abs(live.updatedAt.timeIntervalSince1970 - 1_793_004_936.44) < 0.001)
    #expect(live.fiveHour == UsageWindow(percent: 60, resetsAt: Date(timeIntervalSince1970: 1_793_017_200)))
    #expect(live.sevenDay == UsageWindow(percent: 93, resetsAt: Date(timeIntervalSince1970: 1_793_401_200)))
}

@Test func liveUsageReadsWholeSecondsAndAMissingWindow() throws {
    let live = try #require(LiveUsage(json: Data("""
    {"updated_at": "2026-10-26T08:55:36Z", "seven_day": {"used_percentage": 93.6, "resets_at": "2026-10-30T23:00:00Z"}}
    """.utf8)))
    #expect(live.updatedAt == Date(timeIntervalSince1970: 1_793_004_936))
    #expect(live.fiveHour == nil)
    #expect(live.sevenDay?.percent == 94)
}

@Test func liveUsageNeedsItsTime() {
    #expect(LiveUsage(json: Data(#"{"five_hour": {"used_percentage": 1}}"#.utf8)) == nil)
    #expect(LiveUsage(json: Data(#"{"updated_at": "soon"}"#.utf8)) == nil)
    #expect(LiveUsage(json: Data("{\"updated_".utf8)) == nil)
}

@Test func storeReadsTheLiveSnapshotBesideUsageJSON() throws {
    let store = UsageStore(directory: try temporaryDirectory())
    #expect(store.loadLive() == nil)
    #expect(store.liveURL.deletingLastPathComponent() == store.fileURL.deletingLastPathComponent())
    #expect(store.liveURL.lastPathComponent == "live-usage.json")
    try Data(liveSample.utf8).write(to: store.liveURL)
    #expect(store.loadLive()?.sevenDay?.percent == 93)
}

private func snapshot(at time: Date, week: Date?) -> LiveUsage {
    LiveUsage(updatedAt: time,
              fiveHour: UsageWindow(percent: 70, resetsAt: after(hours: 3)),
              sevenDay: UsageWindow(percent: 40, resetsAt: week))
}

private func usage(_ accounts: [AccountUsage], active: String?) -> UsageFile {
    UsageFile(version: 1, fetchedAt: after(hours: -1), lastError: nil, active: active, unsavedSeat: nil,
              accounts: accounts)
}

@Test func liveNumbersReplaceTheActiveAccountsWhenNewer() {
    let week = after(days: 3)
    let file = usage([account("a", week: week, fetchedAt: after(hours: -1)),
                      account("b", week: after(days: 5), fetchedAt: after(hours: -1))], active: "a")
    // Claude's reset times and dotsync's can differ by a second (08:59:59 against 09:00:00).
    let shown = UsageMerge.withLive(file, snapshot(at: now, week: week.addingTimeInterval(1)))
    #expect(shown.accounts[0].fiveHour?.percent == 70)
    #expect(shown.accounts[0].sevenDay?.percent == 40)
    #expect(shown.accounts[0].fetchedAt == now)
    #expect(shown.accounts[1] == file.accounts[1])
    #expect(shown.fetchedAt == file.fetchedAt)
}

@Test func liveNumbersFromAnotherWeekAreSomeoneElses() {
    // Right after a switch Claude may still hand over the previous account's numbers.
    let file = usage([account("a", week: after(days: 3), fetchedAt: after(hours: -1))], active: "a")
    #expect(UsageMerge.withLive(file, snapshot(at: now, week: after(days: 2))) == file)
    #expect(UsageMerge.withLive(file, snapshot(at: now, week: nil)) == file)
}

@Test func liveNumbersOlderThanTheLastProbeAreIgnored() {
    let week = after(days: 3)
    let file = usage([account("a", week: week, fetchedAt: now)], active: "a")
    #expect(UsageMerge.withLive(file, snapshot(at: after(minutes: -1), week: week)) == file)
}

@Test func liveNumbersNeedASavedActiveAccountThatReads() {
    let week = after(days: 3)
    let live = snapshot(at: now, week: week)
    let noActive = usage([account("a", week: week, fetchedAt: after(hours: -1))], active: nil)
    #expect(UsageMerge.withLive(noActive, live) == noActive)
    let unread = usage([account("a", .error, week: week, fetchedAt: after(hours: -1), error: "timeout")], active: "a")
    #expect(UsageMerge.withLive(unread, live) == unread)
    let file = usage([account("a", week: week, fetchedAt: after(hours: -1))], active: "a")
    #expect(UsageMerge.withLive(file, nil) == file)
}

@Test func aWindowTheSnapshotLacksKeepsItsProbedValue() {
    let week = after(days: 3)
    let file = usage([account("a", week: week, five: 15, fetchedAt: after(hours: -1))], active: "a")
    var live = snapshot(at: now, week: week)
    live.fiveHour = nil
    let shown = UsageMerge.withLive(file, live)
    #expect(shown.accounts[0].fiveHour == file.accounts[0].fiveHour)
    #expect(shown.accounts[0].sevenDay?.percent == 40)
}

@Test func theServiceShowsTheLiveSnapshotButSavesOnlyWhatItProbed() async throws {
    let (service, _) = try makeService()
    let saved = await service.refresh()
    let week = try #require(saved.activeAccount?.sevenDay?.resetsAt)
    let iso = ISO8601DateFormatter()
    try Data("""
    {"updated_at": "\(iso.string(from: Date()))", "seven_day": {"used_percentage": 77, "resets_at": "\(iso.string(from: week))"}}
    """.utf8).write(to: service.store.liveURL)
    #expect(service.shown(saved).activeAccount?.sevenDay?.percent == 77)
    #expect(service.store.load() == saved)
}
