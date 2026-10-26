import Foundation
import Testing
@testable import DotsyncKit

@Test func autoSwitchStartsOffWithoutAFile() throws {
    #expect(AutoSwitchStore(directory: try temporaryDirectory()).loadSettings() == .off)
}

@Test func anUnreadableSettingsFileReadsAsOff() throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    try Data("not json".utf8).write(to: store.settingsURL)
    #expect(store.loadSettings() == .off)
}

@Test func settingsRoundTripWithSnakeCaseKeys() throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    let at = Date(timeIntervalSince1970: 1_800_000_000)
    let settings = AutoSwitchSettings(
        enabled: true, rule: .weeklyReset, excluded: ["bob"], lastSwitchAt: at,
        lastRefetchAt: at.addingTimeInterval(60), restUntil: at.addingTimeInterval(300), restReason: .belowLimit)
    try store.save(settings)

    let json = try #require(
        JSONSerialization.jsonObject(with: Data(contentsOf: store.settingsURL)) as? [String: Any])
    #expect(Set(json.keys) == ["enabled", "rule", "excluded", "last_switch_at", "last_refetch_at",
                               "rest_until", "rest_reason"])
    #expect(json["rule"] as? String == "weekly_reset")
    #expect(json["rest_reason"] as? String == "below_limit")
    #expect(store.settingsURL.lastPathComponent == "auto-switch.json")
    #expect(store.loadSettings() == settings)
}

@Test func updateSettingsStartsFromWhatIsOnDisk() throws {
    // The app's run changes only its own fields: what the person switched
    // meanwhile, written by another save, stays.
    let directory = try temporaryDirectory()
    let mine = AutoSwitchStore(directory: directory)
    var on = AutoSwitchSettings.off
    on.enabled = true
    try mine.save(on)
    try AutoSwitchStore(directory: directory).save(.off)
    let until = Date(timeIntervalSince1970: 1_800_000_300)

    try mine.updateSettings { $0.restUntil = until }

    let settings = mine.loadSettings()
    #expect(settings.enabled == false)
    #expect(settings.restUntil == until)
}

@Test func theLogKeepsTheNewestFiftyFirst() throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    for second in 1...55 {
        try store.append(AutoSwitchLogEntry(
            at: Date(timeIntervalSince1970: TimeInterval(second)), from: "a", to: "b", reason: "5시간 90%",
            rule: .even, result: .switched, message: nil))
    }

    let log = store.loadLog()
    #expect(log.count == 50)
    #expect(log.first?.at == Date(timeIntervalSince1970: 55))
    #expect(log.last?.at == Date(timeIntervalSince1970: 6))
    #expect(store.logURL.lastPathComponent == "auto-switch-log.json")
}

@Test func manualSwitchingIsBlockedOnlyWhileAutoSwitchIsOn() throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    #expect(store.manualSwitchBlock() == nil)
    try store.save(.off)
    #expect(store.manualSwitchBlock() == nil)
    try store.save(AutoSwitchSettings(enabled: true, rule: .even, excluded: []))
    #expect(store.manualSwitchBlock() == "자동 교체가 켜져 있어서 손으로 바꿀 수 없어요")
}

// The menu re-reads what the watch reports: a list changed outside the
// window (another process, a person) must not stay stale there, where a
// click would flip the wrong account (10-26: a real switch to an account
// the window still showed as excluded).

@Test func theWatchSeesSettingsAndTheLogWrittenAgain() async throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    let changes = Counter()
    let watch = try #require(store.watch(queue: DispatchQueue(label: "watch")) { changes.add() })

    try store.save(AutoSwitchSettings(enabled: true, rule: .even, excluded: ["dev"]))
    #expect(await waitUntil { changes.count == 1 })
    // An atomic write replaced the file; the next one is seen too.
    try store.save(AutoSwitchSettings(enabled: true, rule: .even, excluded: []))
    #expect(await waitUntil { changes.count == 2 })
    try store.append(AutoSwitchLogEntry(at: Date(timeIntervalSince1970: 1), from: "a", to: nil, reason: nil,
                                        rule: .even, result: .noCandidate, message: nil))
    #expect(await waitUntil { changes.count == 3 })
    withExtendedLifetime(watch) {}
}

@Test func theWatchIgnoresOtherFilesInTheFolder() async throws {
    // claude-hud rewrites live-usage.json beside it every few seconds.
    let directory = try temporaryDirectory()
    let store = AutoSwitchStore(directory: directory)
    let changes = Counter()
    let watch = try #require(store.watch(queue: DispatchQueue(label: "watch")) { changes.add() })

    try Data("{}".utf8).write(to: directory.appendingPathComponent("live-usage.json"), options: .atomic)
    try await Task.sleep(for: .milliseconds(500))
    #expect(changes.count == 0)
    try store.save(.off)
    #expect(await waitUntil { changes.count == 1 })
    withExtendedLifetime(watch) {}
}

/// True once `condition` holds, false after `limit`.
private func waitUntil(within limit: Duration = .seconds(3), _ condition: () -> Bool) async -> Bool {
    let clock = ContinuousClock()
    let deadline = clock.now + limit
    while !condition() {
        guard clock.now < deadline else { return false }
        try? await Task.sleep(for: .milliseconds(20))
    }
    return true
}
