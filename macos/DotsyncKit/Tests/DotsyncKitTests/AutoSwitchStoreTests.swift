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
