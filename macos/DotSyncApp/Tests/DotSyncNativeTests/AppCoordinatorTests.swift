import AppKit
import Foundation
import SwiftUI
import WebKit
import XCTest
@testable import DotSyncApp
@testable import DotSyncNative

@MainActor
final class AppCoordinatorTests: XCTestCase {
    func testCoordinatorStartsOneBackendForBothSurfaces() async throws {
        let backend = FakeBackendProcess()
        let summary = CountingCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: backend,
            summaryFetcherFactory: { _ in summary }
        )

        await coordinator.start()
        await coordinator.start()
        coordinator.surfaceAppeared(.popover)
        coordinator.surfaceAppeared(.manager)
        coordinator.openManager(.destination(.accounts))

        XCTAssertEqual(backend.startCount, 1)
        XCTAssertEqual(coordinator.managerDestination, .accounts)
        XCTAssertEqual(backend.stopCount, 0)
    }

    func testQuitStopsBackendBeforeTerminatingApplication() async {
        let events = CoordinatorEventRecorder()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(events: events),
            terminator: { events.append("terminate") }
        )

        await coordinator.quit()

        XCTAssertEqual(events.values, ["backend-stop", "terminate"])
    }

    func testQuitDoesNotTerminateWhenStopCannotConfirmExit() async {
        let events = CoordinatorEventRecorder()
        let backend = FakeBackendProcess(
            events: events,
            stopErrors: [.backendExited]
        )
        let coordinator = AppCoordinator(
            backend: backend,
            terminator: { events.append("terminate") }
        )

        await coordinator.quit()

        XCTAssertEqual(events.values, ["backend-stop"])
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        XCTAssertEqual(
            coordinator.recoveryActions,
            [.retry, .openInstallationHelp, .quit]
        )
        XCTAssertFalse(coordinator.isTerminated)
    }

    func testRetryWaitsForOldOwnershipClosureBeforeStartingAgain() async {
        let events = CoordinatorEventRecorder()
        let backend = FakeBackendProcess(
            events: events,
            startErrors: [.backendStartFailed]
        )
        let coordinator = AppCoordinator(backend: backend)
        await coordinator.start()

        await coordinator.retry()

        XCTAssertEqual(
            events.values,
            ["backend-start", "backend-stop", "backend-start"]
        )
        XCTAssertEqual(backend.startCount, 2)
        XCTAssertNil(coordinator.recoveryIssue)
        XCTAssertNotNil(coordinator.session)
    }

    func testRetryDoesNotStartWhileOldOwnershipRemainsUnconfirmed() async {
        let events = CoordinatorEventRecorder()
        let backend = FakeBackendProcess(
            events: events,
            startErrors: [.backendStartFailed],
            stopErrors: [.backendExited]
        )
        let coordinator = AppCoordinator(backend: backend)
        await coordinator.start()

        await coordinator.retry()

        XCTAssertEqual(events.values, ["backend-start", "backend-stop"])
        XCTAssertEqual(backend.startCount, 1)
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
    }

    func testUnexpectedExitShowsRecoveryWithoutLoopRestart() async {
        let backend = FakeBackendProcess()
        let coordinator = AppCoordinator(backend: backend)
        await coordinator.start()

        coordinator.backendExited(.backendExited)
        await Task.yield()

        XCTAssertEqual(backend.startCount, 1)
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        XCTAssertNil(coordinator.session)
    }

    func testClosingManagerLeavesBackendAndMenuExtraRunning() async {
        let backend = FakeBackendProcess()
        let coordinator = AppCoordinator(backend: backend)
        await coordinator.start()
        coordinator.openManager(.destination(.overview))

        coordinator.managerDidClose()

        XCTAssertFalse(coordinator.isManagerPresented)
        XCTAssertNotNil(coordinator.session)
        XCTAssertEqual(backend.stopCount, 0)
    }

    func testOpeningSurfacesDoesNotTriggerSummaryOrProviderWork() async {
        let backend = FakeBackendProcess()
        let summary = CountingCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: backend,
            summaryFetcherFactory: { _ in summary }
        )
        await coordinator.start()
        let initialFetches = await summary.count

        coordinator.surfaceAppeared(.popover)
        coordinator.openManager(.destination(.settings))
        coordinator.surfaceAppeared(.manager)
        await Task.yield()

        let finalFetches = await summary.count
        XCTAssertEqual(initialFetches, 1)
        XCTAssertEqual(finalFetches, initialFetches)
    }

    func testRefreshBridgePerformsExactlyOneCachedSummaryRead() async {
        let summary = CountingCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            summaryFetcherFactory: { _ in summary }
        )
        await coordinator.start()
        let before = await summary.count

        await coordinator.handle(.refreshSummary)

        let after = await summary.count
        XCTAssertEqual(after - before, 1)
    }

    func testApplyAndBackupSurviveTypedSeparateWindowHandoff() async {
        let events = CoordinatorEventRecorder()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            windowOpener: { events.append("open-manager") }
        )

        await coordinator.handle(.openManager(.sync(.apply)))
        let apply = coordinator.managerHandoff
        await coordinator.handle(.openManager(.sync(.backup)))
        let backup = coordinator.managerHandoff

        XCTAssertEqual(coordinator.managerDestination, .sync)
        XCTAssertEqual(apply?.direction, .apply)
        XCTAssertEqual(backup?.direction, .backup)
        XCTAssertEqual(apply?.sequence, 1)
        XCTAssertEqual(backup?.sequence, 2)
        XCTAssertEqual(events.values, ["open-manager", "open-manager"])
    }

    func testBackendErrorsExposeOnlyFixedRecoveryCopy() async {
        let rawFailure = NSError(
            domain: "secret-child-detail",
            code: 17,
            userInfo: [NSLocalizedDescriptionKey: "token path account"]
        )
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(untypedStartError: rawFailure)
        )

        await coordinator.start()

        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        XCTAssertEqual(
            coordinator.recoveryTitle,
            "DotSync could not start its local backend."
        )
        XCTAssertFalse(coordinator.recoveryTitle.contains("secret"))
        XCTAssertFalse(coordinator.recoveryTitle.contains("token"))
        XCTAssertFalse(coordinator.recoveryTitle.contains("path"))
    }

    func testFixtureBackedPopoverAndManagerRootsReuseBackendAndQuitFixture() async throws {
        let fixture = try LoopbackSceneFixture()
        let backend = FakeBackendProcess(
            origin: fixture.origin,
            onStop: { fixture.stop() }
        )
        let coordinator = AppCoordinator(backend: backend)
        await coordinator.start()

        let popover = NSHostingView(
            rootView: PopoverRoot(coordinator: coordinator)
                .frame(width: 360, height: 560)
        )
        popover.frame = NSRect(x: 0, y: 0, width: 360, height: 560)
        popover.layoutSubtreeIfNeeded()

        let manager = NSHostingView(
            rootView: ManagerRoot(coordinator: coordinator)
                .frame(width: 920, height: 620)
        )
        manager.frame = NSRect(x: 0, y: 0, width: 920, height: 620)
        for destination in LocalOrigin.Destination.allCases {
            if destination == .sync {
                coordinator.openManager(.sync(.apply))
            } else {
                coordinator.openManager(.destination(destination))
            }
            manager.layoutSubtreeIfNeeded()
            XCTAssertEqual(coordinator.managerDestination, destination)
        }
        try await Task.sleep(for: .milliseconds(200))

        XCTAssertEqual(backend.startCount, 1)
        XCTAssertFalse(descendants(of: popover, type: WKWebView.self).isEmpty)
        XCTAssertFalse(descendants(of: manager, type: WKWebView.self).isEmpty)
        XCTAssertTrue(fixture.isRunning)

        await coordinator.quit()

        XCTAssertEqual(backend.stopCount, 1)
        XCTAssertFalse(fixture.isRunning)
    }
}

private final class FakeBackendProcess: BackendControlling,
    @unchecked Sendable {
    private let lock = NSLock()
    private let events: CoordinatorEventRecorder?
    private var pendingStartErrors: [BackendError]
    private var pendingStopErrors: [BackendError]
    private var pendingUntypedStartError: Error?
    private let origin: String
    private let onStop: @Sendable () -> Void
    private var starts = 0
    private var stops = 0

    init(
        events: CoordinatorEventRecorder? = nil,
        startErrors: [BackendError] = [],
        stopErrors: [BackendError] = [],
        untypedStartError: Error? = nil,
        origin: String = "http://127.0.0.1:49152",
        onStop: @escaping @Sendable () -> Void = {}
    ) {
        self.events = events
        self.pendingStartErrors = startErrors
        self.pendingStopErrors = stopErrors
        self.pendingUntypedStartError = untypedStartError
        self.origin = origin
        self.onStop = onStop
    }

    var startCount: Int {
        lock.withLock { starts }
    }

    var stopCount: Int {
        lock.withLock { stops }
    }

    func startBackend() async throws -> BackendSession {
        events?.append("backend-start")
        let error: Error? = lock.withLock {
            starts += 1
            if let untyped = pendingUntypedStartError {
                pendingUntypedStartError = nil
                return untyped
            }
            if !pendingStartErrors.isEmpty {
                return pendingStartErrors.removeFirst()
            }
            return nil
        }
        if let error { throw error }
        return BackendSession(origin: try fixtureOrigin())
    }

    func stopBackend() async throws {
        events?.append("backend-stop")
        let error: BackendError? = lock.withLock {
            stops += 1
            guard !pendingStopErrors.isEmpty else { return nil }
            return pendingStopErrors.removeFirst()
        }
        if let error { throw error }
        onStop()
    }

    private func fixtureOrigin() throws -> LocalOrigin {
        try LocalOrigin(
            origin: origin,
            token: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
    }
}

private final class LoopbackSceneFixture: @unchecked Sendable {
    private let lock = NSLock()
    private let process: Process
    private let stopped = DispatchSemaphore(value: 0)
    let origin: String

    init() throws {
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [
            "-u",
            "-c",
            Self.serverProgram,
        ]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [stopped] _ in stopped.signal() }
        try process.run()
        output.fileHandleForWriting.closeFile()

        let ready = DispatchSemaphore(value: 0)
        let line = LockedSceneLine()
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            line.append(data)
            if line.value.contains(0x0a) {
                ready.signal()
            }
        }
        guard ready.wait(timeout: .now() + 2) == .success,
              let newline = line.value.firstIndex(of: 0x0a),
              let port = Int(
                String(
                    decoding: line.value[..<newline],
                    as: UTF8.self
                )
              ),
              (1...65_535).contains(port)
        else {
            process.terminate()
            throw BackendError.backendStartFailed
        }
        output.fileHandleForReading.readabilityHandler = nil
        output.fileHandleForReading.closeFile()
        self.process = process
        self.origin = "http://127.0.0.1:\(port)"
    }

    var isRunning: Bool {
        lock.withLock { process.isRunning }
    }

    func stop() {
        lock.withLock {
            guard process.isRunning else { return }
            process.terminate()
        }
        _ = stopped.wait(timeout: .now() + 2)
    }

    deinit {
        stop()
    }

    private static let serverProgram = #"""
import http.server
import socketserver

BODY = b'''<!doctype html><html><body><main>DotSync Concept A fixture</main><script>
history.replaceState(null, "", "/");
window.addEventListener("dotsync:manager-sync-preview", () => {});
</script></body></html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, format, *args):
        pass

with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
    print(server.server_address[1], flush=True)
    server.serve_forever()
"""#
}

private final class LockedSceneLine: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    var value: Data {
        lock.withLock { data }
    }

    func append(_ value: Data) {
        lock.withLock { data.append(value) }
    }
}

private func descendants<T: NSView>(
    of root: NSView,
    type: T.Type
) -> [T] {
    root.subviews.flatMap { view -> [T] in
        let current = (view as? T).map { [$0] } ?? []
        return current + descendants(of: view, type: type)
    }
}

private final class CoordinatorEventRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [String] = []

    var values: [String] {
        lock.withLock { events }
    }

    func append(_ event: String) {
        lock.withLock { events.append(event) }
    }
}

private actor CountingCoordinatorSummaryFetcher: MenuSummaryFetching {
    private(set) var count = 0

    func fetch() async throws -> MenuSummary {
        count += 1
        return .unknown
    }
}
