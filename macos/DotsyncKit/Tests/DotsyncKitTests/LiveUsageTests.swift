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

@Test func liveUsageReadsWholeSecondsAndADroppedWindow() throws {
    // claude-hud keeps both keys; a window Claude dropped after its reset comes as nulls.
    let live = try #require(LiveUsage(json: Data("""
    {"updated_at": "2026-10-26T08:55:36Z",
     "five_hour": {"used_percentage": null, "resets_at": null},
     "seven_day": {"used_percentage": 93.6, "resets_at": "2026-10-30T23:00:00Z"}}
    """.utf8)))
    #expect(live.updatedAt == Date(timeIntervalSince1970: 1_793_004_936))
    #expect(live.fiveHour == nil)
    #expect(live.sevenDay?.percent == 94)
    #expect(try #require(LiveUsage(json: Data(#"{"updated_at": "2026-10-26T08:55:36Z"}"#.utf8))).sevenDay == nil)
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

private func snapshot(five: UsageWindow?, week: UsageWindow?) -> LiveUsage {
    LiveUsage(updatedAt: now, fiveHour: five, sevenDay: week)
}

private func usage(_ accounts: [AccountUsage], active: String?) -> UsageFile {
    UsageFile(version: 1, fetchedAt: after(hours: -1), lastError: nil, active: active, unsavedSeat: nil,
              accounts: accounts)
}

/// The probed account: 5 hours at 10% until +2h, the week at 20% until +3d.
private func probed(_ name: String = "a") -> AccountUsage {
    account(name, week: after(days: 3), fetchedAt: after(hours: -1))
}

@Test func liveNumbersRaiseTheActiveAccountsWithinTheSameWindows() {
    let file = usage([probed("a"), account("b", week: after(days: 5), fetchedAt: after(hours: -1))], active: "a")
    // Claude's reset times and dotsync's can differ by a second (08:59:59 against 09:00:00).
    let shown = UsageMerge.withLive(file, snapshot(
        five: UsageWindow(percent: 70, resetsAt: after(hours: 2, seconds: 1)),
        week: UsageWindow(percent: 40, resetsAt: after(days: 3, seconds: 1))))
    #expect(shown.accounts[0].fiveHour?.percent == 70)
    #expect(shown.accounts[0].sevenDay?.percent == 40)
    #expect(shown.accounts[1] == file.accounts[1])
    #expect(shown.fetchedAt == file.fetchedAt)
}

@Test func aLowerNumberInTheSameWindowIsAnOldOne() {
    // claude-hud stamps the time it writes: an idle session rewrites its last,
    // older numbers as new. Within one window usage only goes up.
    let file = usage([probed()], active: "a")
    let shown = UsageMerge.withLive(file, snapshot(
        five: UsageWindow(percent: 5, resetsAt: after(hours: 2)),
        week: UsageWindow(percent: 15, resetsAt: after(days: 3))))
    #expect(shown == file)
}

@Test func aLaterFiveHourWindowShowsAnEarlierOneDoesNot() {
    let file = usage([probed()], active: "a")
    let week = UsageWindow(percent: 20, resetsAt: after(days: 3))
    let later = UsageWindow(percent: 3, resetsAt: after(hours: 6))
    #expect(UsageMerge.withLive(file, snapshot(five: later, week: week)).accounts[0].fiveHour == later)
    let earlier = UsageWindow(percent: 90, resetsAt: after(hours: -3))
    #expect(UsageMerge.withLive(file, snapshot(five: earlier, week: week)) == file)
    // A window that started after the probe, which saw none.
    var idle = probed()
    idle.fiveHour = UsageWindow(percent: 0, resetsAt: nil)
    let idleFile = usage([idle], active: "a")
    #expect(UsageMerge.withLive(idleFile, snapshot(five: later, week: week)).accounts[0].fiveHour == later)
}

@Test func aWindowTheSnapshotLacksOrCannotPlaceKeepsItsProbedValue() {
    let file = usage([probed()], active: "a")
    let week = UsageWindow(percent: 40, resetsAt: after(days: 3))
    let without = UsageMerge.withLive(file, snapshot(five: nil, week: week))
    #expect(without.accounts[0].fiveHour == file.accounts[0].fiveHour)
    #expect(without.accounts[0].sevenDay?.percent == 40)
    let unplaced = UsageMerge.withLive(file, snapshot(five: UsageWindow(percent: 70, resetsAt: nil), week: week))
    #expect(unplaced.accounts[0].fiveHour == file.accounts[0].fiveHour)
}

@Test func liveNumbersFromAnotherWeekAreSomeoneElses() {
    // Right after a switch Claude may still hand over the previous account's numbers.
    let file = usage([probed()], active: "a")
    let five = UsageWindow(percent: 70, resetsAt: after(hours: 2))
    #expect(UsageMerge.withLive(file, snapshot(five: five, week: UsageWindow(percent: 40, resetsAt: after(days: 2)))) == file)
    #expect(UsageMerge.withLive(file, snapshot(five: five, week: UsageWindow(percent: 40, resetsAt: nil))) == file)
    #expect(UsageMerge.withLive(file, snapshot(five: five, week: nil)) == file)
}

@Test func aWeekThatRolledOverWaitsForTheNextProbe() {
    // The new week's reset can't be checked against anything dotsync read.
    let file = usage([account("a", week: after(days: -1), fetchedAt: after(days: -2))], active: "a")
    let live = snapshot(five: UsageWindow(percent: 70, resetsAt: after(hours: 2)),
                        week: UsageWindow(percent: 1, resetsAt: after(days: 6)))
    #expect(UsageMerge.withLive(file, live) == file)
}

@Test func liveNumbersNeedASavedActiveAccountThatReads() {
    let live = snapshot(five: UsageWindow(percent: 70, resetsAt: after(hours: 2)),
                        week: UsageWindow(percent: 40, resetsAt: after(days: 3)))
    let noActive = usage([probed()], active: nil)
    #expect(UsageMerge.withLive(noActive, live) == noActive)
    let unread = usage([account("a", .error, week: after(days: 3), fetchedAt: after(hours: -1), error: "timeout")],
                       active: "a")
    #expect(UsageMerge.withLive(unread, live) == unread)
    let file = usage([probed()], active: "a")
    #expect(UsageMerge.withLive(file, nil) == file)
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
