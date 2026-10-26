import Foundation
import Testing
@testable import DotsyncKit

private let t0 = Date(timeIntervalSince1970: 1_793_016_000)  // 2026-10-26 12:00 UTC

/// `dotsync account usage --json` with every account readable; five-hour
/// windows reset at 13:00 and weekly ones on the 29th, both after `t0`.
private func usageReport(active: String? = "a", _ accounts: [(String, five: Int, week: Int)]) -> String {
    let rows = accounts.map { name, five, week in
        """
        {"name": "\(name)", "label": "\(name)", "email": null, "status": "ok",
         "five_hour": {"percent": \(five), "resets_at": "2026-10-26T13:00:00Z"},
         "seven_day": {"percent": \(week), "resets_at": "2026-10-29T12:00:00Z"}}
        """
    }
    return """
    {"fetched_at": "2026-10-26T11:59:00Z", "active": \(active.map { "\"\($0)\"" } ?? "null"),
     "unsaved_seat": null, "accounts": [\(rows.joined(separator: ","))]}
    """
}

/// The service over `makeService`'s fake, with auto switch turned on in the
/// same folder as usage.json.
private func autoSwitch(usage: String, use: String? = nil, useStatus: Int = 0,
                        settings: AutoSwitchSettings = AutoSwitchSettings(enabled: true, rule: .even, excluded: []))
    throws -> (AutoSwitchService, AutoSwitchStore, FakeDotsync) {
    let (accounts, fake) = try makeService(usage: usage, use: use, useStatus: useStatus)
    let store = AutoSwitchStore(directory: accounts.store.fileURL.deletingLastPathComponent())
    try store.save(settings)
    return (AutoSwitchService(accounts: accounts, store: store, gate: RefreshGate()), store, fake)
}

private func useCalls(_ fake: FakeDotsync) -> [[String]] {
    fake.calls().filter { $0.count > 1 && $0[1] == "use" }
}

@Test func runSwitchesToThePickedAccountAndLogsIt() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 93, 20), ("b", 10, 40), ("c", 10, 20)]))

    let entry = await service.run(.switchNow, now: t0)

    #expect(useCalls(fake).map(\.last) == ["c"])
    #expect(entry == AutoSwitchLogEntry(at: t0, from: "a", to: "c", reason: "5시간 93%", rule: .even,
                                        result: .switched, message: nil))
    #expect(store.loadLog() == [entry!])
    #expect(store.loadSettings().lastSwitchAt == t0)
    #expect(store.loadSettings().restUntil == nil)
}

@Test func runDoesNothingWhenOff() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 93, 20), ("b", 10, 10)]), settings: .off)

    #expect(await service.run(.switchNow, now: t0) == nil)
    #expect(fake.calls().isEmpty)
    #expect(store.loadLog().isEmpty)
}

@Test func runCountsAFailedProbeAsAFailure() async throws {
    // A failed probe leaves the old numbers in usage.json; they are no reading.
    let (service, store, fake) = try autoSwitch(usage: "not json")

    let entry = await service.run(.switchNow, now: t0)

    #expect(useCalls(fake).isEmpty)
    #expect(entry?.result == .failed)
    #expect(entry?.message == "dotsync의 답을 읽을 수 없어요")
    #expect(store.loadSettings().restUntil == t0.addingTimeInterval(900))
    #expect(store.loadSettings().restReason == .failed)
}

@Test func runJudgesByTheProbeNotTheLiveValue() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 80, 20), ("b", 10, 10)]))
    try Data("""
    {"updated_at": "2026-10-26T11:59:30.000Z",
     "five_hour": {"used_percentage": 95, "resets_at": "2026-10-26T13:00:00Z"},
     "seven_day": {"used_percentage": 20, "resets_at": "2026-10-29T12:00:00Z"}}
    """.utf8).write(to: service.accounts.store.liveURL)

    #expect(await service.run(.switchNow, now: t0)?.result == .belowLimit)
    #expect(useCalls(fake).isEmpty)
    #expect(store.loadSettings().restReason == .belowLimit)
}

@Test func runRestsFiveMinutesWhenTheProbeIsBelowTheLimit() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 80, 20), ("b", 10, 10)]))

    let entry = await service.run(.switchNow, now: t0)

    #expect(useCalls(fake).isEmpty)
    #expect(entry == AutoSwitchLogEntry(at: t0, from: "a", to: nil, reason: nil, rule: .even,
                                        result: .belowLimit, message: nil))
    #expect(store.loadSettings().restUntil == t0.addingTimeInterval(300))
    #expect(store.loadSettings().restReason == .belowLimit)
}

@Test func runRestsFifteenMinutesWithoutACandidate() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 93, 20), ("b", 95, 10), ("c", 95, 10)]))

    let entry = await service.run(.switchNow, now: t0)

    #expect(useCalls(fake).isEmpty)
    #expect(entry?.result == .noCandidate)
    #expect(entry?.reason == "5시간 93%")
    #expect(store.loadSettings().restUntil == t0.addingTimeInterval(900))
    #expect(store.loadSettings().restReason == .noCandidate)
}

@Test func runKeepsChangesMadeWhileItRuns() async throws {
    // The person turns auto switch off (and picks the other rule) while the
    // probe runs: nothing switches, and what they chose stays.
    let paths = try FakeDotsync(script: "")
    let directory = try temporaryDirectory()
    let store = AutoSwitchStore(directory: directory)
    try store.save(AutoSwitchSettings(enabled: true, rule: .even, excluded: []))
    let report = try paths.file("usage.json", usageReport([("a", 93, 20), ("b", 10, 10)]))
    let off = try paths.file("off.json", #"{"enabled": false, "rule": "weekly_reset", "excluded": []}"#)
    let fake = try FakeDotsync(script: """
    case "$2" in
      usage) cp '\(off)' '\(store.settingsURL.path)'; cat '\(report)' ;;
      *) echo '{"name": "b", "label": "b", "email": null}' ;;
    esac
    """)
    let accounts = AccountService(cli: fake.cli, store: UsageStore(directory: directory))
    let service = AutoSwitchService(accounts: accounts, store: store, gate: RefreshGate())

    #expect(await service.run(.switchNow, now: t0) == nil)
    #expect(useCalls(fake).isEmpty)
    #expect(store.loadSettings().enabled == false)
    #expect(store.loadSettings().rule == .weeklyReset)
    #expect(store.loadLog().isEmpty)
}

@Test func runStopsOnAnUnsavedLogin() async throws {
    let (service, store, _) = try autoSwitch(
        usage: usageReport([("a", 93, 20), ("b", 10, 10)]),
        use: #"{"error": {"code": "unsaved_login", "message": "m", "email": "x@y"}}"#, useStatus: 1)

    let entry = await service.run(.switchNow, now: t0)

    #expect(entry?.result == .unsavedLogin)
    #expect(store.loadSettings().restUntil == nil)
    #expect(store.loadSettings().lastSwitchAt == nil)
}

@Test func runRestsFifteenMinutesWhenTheSwitchFails() async throws {
    let (service, store, _) = try autoSwitch(
        usage: usageReport([("a", 93, 20), ("b", 10, 10)]),
        use: #"{"error": {"code": "failed", "message": "boom"}}"#, useStatus: 1)

    let entry = await service.run(.switchNow, now: t0)

    #expect(entry?.result == .failed)
    #expect(entry?.message == "boom")
    #expect(store.loadSettings().restUntil == t0.addingTimeInterval(900))
    #expect(store.loadSettings().restReason == .failed)
}

@Test func aRefetchBelowTheLimitWritesNoLog() async throws {
    let (service, store, _) = try autoSwitch(usage: usageReport([("a", 50, 20), ("b", 10, 10)]))

    #expect(await service.run(.refetch, now: t0) == nil)
    #expect(store.loadLog().isEmpty)
    #expect(store.loadSettings().lastRefetchAt == t0)
    #expect(store.loadSettings().restUntil == nil)
}

@Test func aRefetchOverTheLimitSwitches() async throws {
    let (service, store, fake) = try autoSwitch(usage: usageReport([("a", 10, 99), ("b", 10, 10)]))

    let entry = await service.run(.refetch, now: t0)

    #expect(entry?.result == .switched)
    #expect(entry?.reason == "주간 99%")
    #expect(useCalls(fake).map(\.last) == ["b"])
    #expect(store.loadSettings().lastRefetchAt == t0)
}

@Test func aMissingDotsyncRestsAndIsLogged() throws {
    let store = AutoSwitchStore(directory: try temporaryDirectory())
    try store.save(AutoSwitchSettings(enabled: true, rule: .weeklyReset, excluded: []))

    let entry = AutoSwitchService.recordUnavailable(store, message: "dotsync를 찾을 수 없어요", now: t0)

    #expect(entry == AutoSwitchLogEntry(at: t0, from: nil, to: nil, reason: nil, rule: .weeklyReset,
                                        result: .failed, message: "dotsync를 찾을 수 없어요"))
    #expect(store.loadLog() == [entry])
    #expect(store.loadSettings().restUntil == t0.addingTimeInterval(900))
    #expect(store.loadSettings().restReason == .failed)
    #expect(store.loadSettings().enabled)
}
