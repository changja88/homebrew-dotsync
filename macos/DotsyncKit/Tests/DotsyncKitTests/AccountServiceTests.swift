import Foundation
import Testing
@testable import DotsyncKit

/// A fake dotsync answering per subcommand ($2) from files, plus a store.
func makeService(usage: String = sampleReport, use: String? = nil, useStatus: Int = 0,
                 other: String = #"{"name": "bob", "label": "밥", "email": "b@x", "logged_in": true}"#)
    throws -> (AccountService, FakeDotsync) {
    let paths = try FakeDotsync(script: "")
    let usagePath = try paths.file("usage.json", usage)
    let usePath = try paths.file("use.json", use ?? other)
    let otherPath = try paths.file("other.json", other)
    let fake = try FakeDotsync(script: """
    case "$2" in
      usage) cat '\(usagePath)' ;;
      use) cat '\(usePath)'; exit \(useStatus) ;;
      *) cat '\(otherPath)' ;;
    esac
    """)
    return (AccountService(cli: fake.cli, store: UsageStore(directory: try temporaryDirectory())), fake)
}

@Test func refreshMergesTheReportAndSavesIt() async throws {
    let (service, _) = try makeService()
    let file = await service.refresh()
    #expect(file.active == "onelife")
    #expect(service.store.load() == file)
}

@Test func aFailedRefreshKeepsTheOldFileAndSaysWhy() async throws {
    let (service, _) = try makeService(usage: "not json")
    var previous = UsageFile.empty
    previous.active = "kept"
    try service.store.save(previous)

    let file = await service.refresh()

    #expect(file.active == "kept")
    #expect(file.lastError == "dotsync의 답을 읽을 수 없어요")
}

@Test func aCancelledRefreshLeavesTheFileAlone() async throws {
    // Closing the window cancels its refresh. dotsync is stopped before it
    // answers; that is not a failure for the widget to show.
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

    let refresh = Task { await service.refresh() }
    while fake.calls().isEmpty { try await Task.sleep(for: .milliseconds(20)) }
    refresh.cancel()
    let file = await refresh.value

    #expect(file == previous)
    #expect(service.store.load() == previous)
}

@Test func useSwitchesThenRefreshes() async throws {
    let (service, fake) = try makeService()
    guard case .switched(let file) = await service.use("onelife") else {
        Issue.record("expected a switch"); return
    }
    #expect(file.active == "onelife")
    #expect(fake.calls().map { $0[1] } == ["use", "usage"])
}

@Test func useOfAnUnsavedLoginAsksFirstAndChangesNothing() async throws {
    let (service, fake) = try makeService(
        use: #"{"error": {"code": "unsaved_login", "message": "m", "email": "u@x"}}"#, useStatus: 1)
    try service.store.save(.empty)
    #expect(await service.use("bob") == .needsConfirmation(email: "u@x"))
    #expect(service.store.load() == .empty)
    #expect(fake.calls().map { $0[1] } == ["use"])
}

@Test func useFailuresComeBack() async throws {
    let (service, _) = try makeService(
        use: #"{"error": {"code": "login_required", "message": "bob is not logged in"}}"#, useStatus: 1)
    #expect(await service.use("bob") == .failed(CLIError(code: "login_required", message: "bob is not logged in")))
}

@Test func renameUpdatesTheLabelInPlace() async throws {
    let (service, _) = try makeService()
    var file = UsageFile.empty
    file.accounts = [AccountUsage(name: "bob", label: "bob", email: "b@x", status: .ok)]
    try service.store.save(file)

    let renamed = try await service.rename("bob", to: "밥").get()

    #expect(renamed.accounts[0].label == "밥")
    #expect(service.store.load() == renamed)
}

@Test func removeDropsTheAccount() async throws {
    let (service, _) = try makeService(other: #"{"removed": "bob"}"#)
    var file = UsageFile.empty
    file.accounts = [AccountUsage(name: "bob", label: "bob", email: nil, status: .ok)]
    try service.store.save(file)

    let left = try await service.remove("bob").get()

    #expect(left.accounts.isEmpty)
}

@Test func reportSavesAFailureMessage() async throws {
    let (service, _) = try makeService()
    service.report(failure: "dotsync를 찾을 수 없어요")
    #expect(service.store.load()?.lastError == "dotsync를 찾을 수 없어요")
}
