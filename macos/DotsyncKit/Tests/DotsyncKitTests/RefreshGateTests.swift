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
