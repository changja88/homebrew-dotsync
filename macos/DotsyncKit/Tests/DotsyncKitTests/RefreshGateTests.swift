import Foundation
import Testing
@testable import DotsyncKit

@Test func refreshesThatOverlapRunDotsyncOnce() async throws {
    // Pressing ↻ again while a refresh runs waits for that one instead of
    // queueing another 8-second dotsync behind the lock.
    let paths = try FakeDotsync(script: "")
    let report = try paths.file("usage.json", sampleReport)
    let fake = try FakeDotsync(script: "sleep 0.5; cat '\(report)'")
    let service = AccountService(cli: fake.cli, store: UsageStore(directory: try temporaryDirectory()))
    let gate = RefreshGate()

    async let first = gate.refresh(service)
    async let second = gate.refresh(service)
    let files = await [first, second]

    #expect(fake.calls().count == 1)
    #expect(files[0] == files[1])

    _ = await gate.refresh(service)
    #expect(fake.calls().count == 2)
}

@Test func quittingLetsARunningRefreshFinishAndSave() async throws {
    // Closing the window quits the app; the refresh it started must not stop
    // halfway and leave the widget on 조회 중… without the result.
    let paths = try FakeDotsync(script: "")
    let report = try paths.file("usage.json", sampleReport)
    let fake = try FakeDotsync(script: "sleep 0.3; cat '\(report)'")
    let service = AccountService(cli: fake.cli, store: UsageStore(directory: try temporaryDirectory()))
    let gate = RefreshGate()

    let refresh = Task { await gate.refresh(service) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    await gate.settle(within: .seconds(10))

    let saved = try #require(service.store.load())
    #expect(saved.refreshingSince == nil)
    #expect(saved.fetchedAt != nil)
    _ = await refresh.value
}

@Test func quittingCancelsARefreshThatRunsTooLong() async throws {
    let fake = try FakeDotsync(script: """
    sleep 30 >/dev/null 2>&1 &
    child=$!
    trap 'kill $child; exit 143' TERM
    wait $child
    """)
    let service = AccountService(cli: fake.cli, store: UsageStore(directory: try temporaryDirectory()))
    var previous = UsageFile.empty
    previous.active = "kept"
    try service.store.save(previous)
    let gate = RefreshGate()

    let refresh = Task { await gate.refresh(service) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    let start = Date()
    await gate.settle(within: .milliseconds(200))

    #expect(Date().timeIntervalSince(start) < 5)
    #expect(service.store.load() == previous)
    _ = await refresh.value
}

@Test func quittingWithNoRefreshDoesNotWait() async {
    let start = Date()
    await RefreshGate().settle(within: .seconds(30))
    #expect(Date().timeIntervalSince(start) < 1)
}

@Test func theGateSaysWhileARefreshRuns() async throws {
    // The app asks this when it is about to quit, without waiting on the actor.
    let paths = try FakeDotsync(script: "")
    let report = try paths.file("usage.json", sampleReport)
    let fake = try FakeDotsync(script: "sleep 0.3; cat '\(report)'")
    let service = AccountService(cli: fake.cli, store: UsageStore(directory: try temporaryDirectory()))
    let gate = RefreshGate()
    #expect(!gate.isRunning)

    let refresh = Task { await gate.refresh(service) }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    #expect(gate.isRunning)
    _ = await refresh.value
    #expect(!gate.isRunning)
}
