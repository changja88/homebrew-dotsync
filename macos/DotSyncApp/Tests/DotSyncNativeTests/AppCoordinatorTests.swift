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

    func testRapidRetriesShareOneStopThenStartOperation() async throws {
        let events = CoordinatorEventRecorder()
        let stopBarrier = BackendStopBarrier()
        let backend = FakeBackendProcess(
            events: events,
            stopBarrier: stopBarrier
        )
        let coordinator = AppCoordinator(backend: backend)
        let first = Task { @MainActor in
            await coordinator.retry()
        }
        try await eventually { backend.stopCount == 1 }

        let secondEntered = expectation(description: "second retry entered")
        let second = Task { @MainActor in
            secondEntered.fulfill()
            await coordinator.retry()
        }
        await fulfillment(of: [secondEntered], timeout: 1)

        XCTAssertEqual(backend.stopCount, 1)
        XCTAssertEqual(backend.startCount, 0)
        await stopBarrier.releaseAll()
        await first.value
        await second.value

        XCTAssertEqual(events.values, ["backend-stop", "backend-start"])
        XCTAssertEqual(backend.stopCount, 1)
        XCTAssertEqual(backend.startCount, 1)
        XCTAssertNotNil(coordinator.session)
    }

    func testFailedSharedRetryCleansUpSoSubsequentRetryCanRun() async throws {
        let events = CoordinatorEventRecorder()
        let stopBarrier = BackendStopBarrier()
        let backend = FakeBackendProcess(
            events: events,
            stopErrors: [.backendExited],
            stopBarrier: stopBarrier
        )
        let coordinator = AppCoordinator(backend: backend)
        let first = Task { @MainActor in
            await coordinator.retry()
        }
        try await eventually { backend.stopCount == 1 }
        let joinedEntered = expectation(description: "joined retry entered")
        let joined = Task { @MainActor in
            joinedEntered.fulfill()
            await coordinator.retry()
        }
        await fulfillment(of: [joinedEntered], timeout: 1)

        XCTAssertEqual(backend.stopCount, 1)
        await stopBarrier.releaseAll()
        await first.value
        await joined.value
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        XCTAssertEqual(backend.startCount, 0)

        let subsequentEntered = expectation(
            description: "subsequent retry entered"
        )
        let subsequent = Task { @MainActor in
            subsequentEntered.fulfill()
            await coordinator.retry()
        }
        await fulfillment(of: [subsequentEntered], timeout: 1)
        XCTAssertEqual(backend.stopCount, 2)
        await stopBarrier.releaseAll()
        await subsequent.value

        XCTAssertEqual(
            events.values,
            ["backend-stop", "backend-stop", "backend-start"]
        )
        XCTAssertEqual(backend.stopCount, 2)
        XCTAssertEqual(backend.startCount, 1)
        XCTAssertNil(coordinator.recoveryIssue)
        XCTAssertNotNil(coordinator.session)
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

    func testSlowInitialSummaryCannotOverwriteNewerExplicitReload() async throws {
        let fetcher = ControllableCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            summaryFetcherFactory: { _ in fetcher }
        )
        let initial = Task { @MainActor in
            await coordinator.start()
        }
        try await eventually { fetcher.callCount == 1 }

        let reload = Task { @MainActor in
            await coordinator.handle(.refreshSummary)
        }
        try await eventually { fetcher.callCount == 2 }
        fetcher.succeed(call: 2, with: coordinatorSummary(percent: 82))
        await reload.value
        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 82)

        fetcher.succeed(call: 1, with: coordinatorSummary(percent: 14))
        await initial.value

        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 82)
    }

    func testCadenceRejectedPollDoesNotInvalidateInFlightInitialSummary() async throws {
        let fetcher = ControllableCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            summaryFetcherFactory: { _ in fetcher }
        )
        let initial = Task { @MainActor in
            await coordinator.start()
        }
        try await eventually { fetcher.callCount == 1 }

        await coordinator.pollSummaryIfDue()
        XCTAssertEqual(fetcher.callCount, 1)
        fetcher.succeed(call: 1, with: coordinatorSummary(percent: 41))
        await initial.value

        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 41)
    }

    func testCadenceRejectedPollDoesNotInvalidateInFlightExplicitSummary() async throws {
        let fetcher = ControllableCoordinatorSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            summaryFetcherFactory: { _ in fetcher }
        )
        let initial = Task { @MainActor in
            await coordinator.start()
        }
        try await eventually { fetcher.callCount == 1 }
        fetcher.succeed(call: 1, with: coordinatorSummary(percent: 12))
        await initial.value

        let reload = Task { @MainActor in
            await coordinator.handle(.refreshSummary)
        }
        try await eventually { fetcher.callCount == 2 }
        await coordinator.pollSummaryIfDue()
        XCTAssertEqual(fetcher.callCount, 2)
        fetcher.succeed(call: 2, with: coordinatorSummary(percent: 73))
        await reload.value

        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 73)
    }

    func testSummaryCommitRevalidatesOwnershipAfterFinalActorHop() async {
        let newestCheck = SummaryNewestCheckBarrier()
        var requestIsStillOwned = true
        var committed = false

        let staleCommit = Task { @MainActor in
            await commitAfterNewestSummaryCheck(
                isNewest: {
                    await newestCheck.pauseThenReturnNewest()
                },
                isStillOwned: {
                    requestIsStillOwned
                },
                commit: {
                    committed = true
                }
            )
        }
        await newestCheck.waitUntilPaused()

        requestIsStillOwned = false
        await newestCheck.release()
        await staleCommit.value

        XCTAssertFalse(committed)
    }

    func testOldSessionSummaryCannotOverwriteReplacementSessionResult() async throws {
        let oldFetcher = ControllableCoordinatorSummaryFetcher()
        let newFetcher = ControllableCoordinatorSummaryFetcher()
        let factory = CoordinatorSummaryFetcherFactory(
            fetchers: [oldFetcher, newFetcher]
        )
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            summaryFetcherFactory: { _ in factory.next() }
        )
        let oldStart = Task { @MainActor in
            await coordinator.start()
        }
        try await eventually { oldFetcher.callCount == 1 }

        coordinator.backendExited(.backendExited)
        let retry = Task { @MainActor in
            await coordinator.retry()
        }
        try await eventually { newFetcher.callCount == 1 }
        newFetcher.succeed(call: 1, with: coordinatorSummary(percent: 67))
        await retry.value
        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 67)

        oldFetcher.fail(call: 1, with: .backendProtocolError)
        await oldStart.value

        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 67)
        XCTAssertNotNil(coordinator.session)
        XCTAssertNil(coordinator.recoveryIssue)
    }

    func testApplyAndBackupSurviveTypedSeparateWindowHandoff() async {
        let events = CoordinatorEventRecorder()
        let coordinator = AppCoordinator(
            backend: FakeBackendProcess(),
            windowOpener: { events.append("open-manager") }
        )

        await coordinator.handle(.openManager(.sync(.apply)))
        await coordinator.handle(.openManager(.sync(.backup)))

        XCTAssertEqual(coordinator.managerDestination, .sync)
        XCTAssertEqual(
            coordinator.managerHandoffs,
            [
                ManagerSyncHandoff(sequence: 1, direction: .apply),
                ManagerSyncHandoff(sequence: 2, direction: .backup),
            ]
        )
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
        manager.layoutSubtreeIfNeeded()
        let popoverWebView = try XCTUnwrap(
            descendants(of: popover, type: WKWebView.self).first
        )
        let managerWebView = try XCTUnwrap(
            descendants(of: manager, type: WKWebView.self).first
        )
        do {
            try await eventually(timeout: .seconds(5)) {
                popover.layoutSubtreeIfNeeded()
                return fixture.hasLaunch(
                    surface: "popover",
                    destination: "overview"
                ) && popoverWebView.url?.query == nil
            }
        } catch {
            await coordinator.quit()
            XCTFail(
                "Popover fixture launch did not finish; "
                    + "launches=\(fixture.launches), "
                    + "url=\(popoverWebView.url?.absoluteString ?? "nil"), "
                    + "loading=\(popoverWebView.isLoading), "
                    + "queryErased=\(popoverWebView.url?.query == nil)"
            )
            return
        }

        let requests: [ManagerRequest] = [
            .destination(.overview),
            .destination(.accounts),
            .sync(.apply),
            .destination(.settings),
        ]
        for request in requests {
            coordinator.openManager(request)
            manager.layoutSubtreeIfNeeded()
            let destination = request.destination.rawValue
            let expectedHeading = [
                "overview": "Overview",
                "accounts": "Accounts",
                "sync": "Config Sync",
                "settings": "Settings",
            ][destination]
            var observedHeading: String?
            var observedPreviewHeading: String?
            do {
                try await eventuallyAsync(timeout: .seconds(5)) {
                    manager.layoutSubtreeIfNeeded()
                    observedHeading = try? await managerWebView
                        .evaluateJavaScript(
                            "document.querySelector('#manager-content h2')?.textContent"
                        ) as? String
                    if destination == "sync" {
                        observedPreviewHeading = try? await managerWebView
                            .evaluateJavaScript(
                                "document.querySelector('.preview-panel .panel-heading strong')?.textContent"
                            ) as? String
                    }
                    let syncWasDelivered = destination != "sync"
                        || (
                            fixture.eventDirections.contains("apply")
                                && observedPreviewHeading == "Apply preview"
                                && coordinator.managerHandoffs.isEmpty
                        )
                    return fixture.hasLaunch(
                        surface: "manager",
                        destination: destination
                    )
                        && observedHeading == expectedHeading
                        && managerWebView.url?.query == nil
                        && syncWasDelivered
                }
            } catch {
                let launches = fixture.managerLaunches.map(\.destination)
                let events = fixture.eventDirections
                let queryErased = managerWebView.url?.query == nil
                await coordinator.quit()
                XCTFail(
                    "Manager fixture destination \(destination) did not finish; "
                        + "launches=\(launches), events=\(events), "
                        + "heading=\(observedHeading ?? "nil"), "
                        + "preview=\(observedPreviewHeading ?? "nil"), "
                        + "queryErased=\(queryErased)"
                )
                return
            }
            XCTAssertEqual(
                managerWebView.url?.absoluteString,
                fixture.origin + "/"
            )
        }

        XCTAssertEqual(backend.startCount, 1)
        XCTAssertEqual(
            fixture.managerLaunches.map(\.destination),
            ["overview", "accounts", "sync", "settings"]
        )
        XCTAssertTrue(
            fixture.launches.allSatisfy {
                $0.queryKeys == ["destination", "surface", "token"]
                    && $0.tokenLength == 43
            }
        )
        XCTAssertEqual(fixture.eventDirections, ["apply"])
        XCTAssertTrue(coordinator.managerHandoffs.isEmpty)
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
    private let stopBarrier: BackendStopBarrier?
    private var starts = 0
    private var stops = 0

    init(
        events: CoordinatorEventRecorder? = nil,
        startErrors: [BackendError] = [],
        stopErrors: [BackendError] = [],
        untypedStartError: Error? = nil,
        origin: String = "http://127.0.0.1:49152",
        onStop: @escaping @Sendable () -> Void = {},
        stopBarrier: BackendStopBarrier? = nil
    ) {
        self.events = events
        self.pendingStartErrors = startErrors
        self.pendingStopErrors = stopErrors
        self.pendingUntypedStartError = untypedStartError
        self.origin = origin
        self.onStop = onStop
        self.stopBarrier = stopBarrier
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
        await stopBarrier?.wait()
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

private actor BackendStopBarrier {
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func releaseAll() {
        let current = waiters
        waiters.removeAll()
        for continuation in current {
            continuation.resume()
        }
    }
}

private final class LoopbackSceneFixture: @unchecked Sendable {
    struct Launch: Equatable {
        let surface: String
        let destination: String
        let tokenLength: Int
        let queryKeys: [String]
    }

    private let lock = NSLock()
    private let process: Process
    private let output: Pipe
    private let transcript: LockedSceneTranscript
    private let stopped = DispatchSemaphore(value: 0)
    let origin: String

    init() throws {
        let output = Pipe()
        let transcript = LockedSceneTranscript()
        let process = Process()
        self.output = output
        self.transcript = transcript
        self.process = process
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [
            "-u",
            "-c",
            Self.serverProgram,
            Self.staticDirectory.path,
        ]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [stopped] _ in stopped.signal() }
        try process.run()
        output.fileHandleForWriting.closeFile()

        let ready = DispatchSemaphore(value: 0)
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            transcript.append(data)
            if transcript.lines.contains(where: { $0.hasPrefix("PORT\t") }) {
                ready.signal()
            }
        }
        guard ready.wait(timeout: .now() + 2) == .success,
              let portLine = transcript.lines.first(
                where: { $0.hasPrefix("PORT\t") }
              ),
              let port = Int(portLine.dropFirst("PORT\t".count)),
              (1...65_535).contains(port)
        else {
            process.terminate()
            throw BackendError.backendStartFailed
        }
        self.origin = "http://127.0.0.1:\(port)"
    }

    var launches: [Launch] {
        transcript.lines.compactMap { line in
            let fields = line.split(
                separator: "\t",
                omittingEmptySubsequences: false
            )
            guard fields.count == 6,
                  fields[0] == "LAUNCH",
                  let tokenLength = Int(fields[3]),
                  let keyCount = Int(fields[4])
            else { return nil }
            let keys = fields[5].split(separator: ",").map(String.init)
            guard keys.count == keyCount else { return nil }
            return Launch(
                surface: String(fields[1]),
                destination: String(fields[2]),
                tokenLength: tokenLength,
                queryKeys: keys
            )
        }
    }

    var managerLaunches: [Launch] {
        launches.filter { $0.surface == "manager" }
    }

    var eventDirections: [String] {
        transcript.lines.compactMap { line in
            let fields = line.split(separator: "\t")
            guard fields.count == 2, fields[0] == "EVENT"
            else { return nil }
            return String(fields[1])
        }
    }

    func hasLaunch(surface: String, destination: String) -> Bool {
        launches.contains {
            $0.surface == surface && $0.destination == destination
        }
    }

    var isRunning: Bool {
        lock.withLock { process.isRunning }
    }

    func stop() {
        let requestedTermination = lock.withLock {
            guard process.isRunning else { return false }
            process.terminate()
            return true
        }
        guard requestedTermination else { return }
        _ = stopped.wait(timeout: .now() + 2)
        output.fileHandleForReading.readabilityHandler = nil
        try? output.fileHandleForReading.close()
    }

    deinit {
        stop()
    }

    private static let serverProgram = #"""
import http.server
import json
import pathlib
import socketserver
import sys
import urllib.parse

ALLOWED_SURFACES = {"popover", "manager"}
ALLOWED_DESTINATIONS = {"overview", "accounts", "sync", "settings"}
STATIC_ROOT = pathlib.Path(sys.argv[1])
ASSETS = {
    "/app.mjs": ("text/javascript; charset=utf-8", "app.mjs"),
    "/api-client.mjs": ("text/javascript; charset=utf-8", "api-client.mjs"),
    "/render.mjs": ("text/javascript; charset=utf-8", "render.mjs"),
    "/state.mjs": ("text/javascript; charset=utf-8", "state.mjs"),
    "/styles.css": ("text/css; charset=utf-8", "styles.css"),
}

def one(query, key):
    values = query.get(key, [])
    return values[0] if len(values) == 1 else ""

class Handler(http.server.BaseHTTPRequestHandler):
    def send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload):
        self.send_bytes(
            200,
            "application/json; charset=utf-8",
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )

    def authorized(self):
        token = self.headers.get("X-DotSync-Token", "")
        return len(token) == 43 and token.replace("_", "").replace("-", "").isalnum()

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        if parts.path in ASSETS and not parts.query:
            content_type, name = ASSETS[parts.path]
            self.send_bytes(200, content_type, (STATIC_ROOT / name).read_bytes())
            return
        if parts.path.startswith("/api/"):
            if parts.query or not self.authorized():
                self.send_error(403)
                return
            if parts.path == "/api/bootstrap":
                self.send_json({"providers": {}, "sync_configured": True})
                return
            if parts.path == "/api/accounts":
                self.send_json({"accounts": []})
                return
            if parts.path == "/api/menu-summary":
                self.send_json({
                    "usage": {"state": "unknown", "highest_percent": None},
                    "sync": {"state": "unknown", "attention_count": None},
                    "observed_at": None,
                })
                return
            if parts.path == "/api/sync/status":
                self.send_json({"sync": {
                    "sync_dir": {"scope": "sync-root", "id": "sha256:" + "a" * 64},
                    "apps": [{"name": "zsh", "state": "clean", "direction": None}],
                }})
                return
            self.send_error(404)
            return
        if parts.path == "/":
            surface = one(query, "surface")
            destination = one(query, "destination")
            token = one(query, "token")
            keys = sorted(query)
            if (surface not in ALLOWED_SURFACES or
                    destination not in ALLOWED_DESTINATIONS or
                    keys != ["destination", "surface", "token"]):
                self.send_error(400)
                return
            print(
                "LAUNCH\t%s\t%s\t%d\t%d\t%s" % (
                    surface,
                    destination,
                    len(token),
                    len(keys),
                    ",".join(keys),
                ),
                flush=True,
            )
            self.send_bytes(
                200,
                "text/html; charset=utf-8",
                (STATIC_ROOT / "index.html").read_bytes(),
            )
            return
        self.send_error(404)

    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path)
        if (parts.path != "/api/sync/preview" or parts.query or
                not self.authorized()):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        if (type(payload) is not dict or sorted(payload) != ["apps", "direction"] or
                payload["direction"] not in {"backup", "apply"} or
                payload["apps"] != ["zsh"]):
            self.send_error(400)
            return
        direction = payload["direction"]
        print("EVENT\t%s" % direction, flush=True)
        self.send_json({"preview": {
            "direction": direction,
            "apps": ["zsh"],
            "digest": "b" * 64,
            "plans": [],
        }})

    def log_message(self, format, *args):
        pass

class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

with Server(("127.0.0.1", 0), Handler) as server:
    print("PORT\t%d" % server.server_address[1], flush=True)
    server.serve_forever()
"""#

    private static var staticDirectory: URL {
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 {
            root.deleteLastPathComponent()
        }
        return root.appendingPathComponent(
            "lib/dotsync/web/static",
            isDirectory: true
        )
    }
}

private final class LockedSceneTranscript: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    var lines: [String] {
        lock.withLock {
            String(decoding: data, as: UTF8.self)
                .split(separator: "\n")
                .map(String.init)
        }
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

private actor SummaryNewestCheckBarrier {
    private var checkContinuation: CheckedContinuation<Bool, Never>?
    private var observerContinuations: [CheckedContinuation<Void, Never>] = []

    func pauseThenReturnNewest() async -> Bool {
        await withCheckedContinuation { continuation in
            checkContinuation = continuation
            let observers = observerContinuations
            observerContinuations.removeAll()
            observers.forEach { $0.resume() }
        }
    }

    func waitUntilPaused() async {
        guard checkContinuation == nil else { return }
        await withCheckedContinuation { continuation in
            observerContinuations.append(continuation)
        }
    }

    func release() {
        let continuation = checkContinuation
        checkContinuation = nil
        continuation?.resume(returning: true)
    }
}

private final class ControllableCoordinatorSummaryFetcher:
    MenuSummaryFetching, @unchecked Sendable {
    private let lock = NSLock()
    private var calls = 0
    private var continuations: [
        Int: CheckedContinuation<MenuSummary, any Error>
    ] = [:]

    var callCount: Int {
        lock.withLock { calls }
    }

    func fetch() async throws -> MenuSummary {
        try await withCheckedThrowingContinuation { continuation in
            lock.withLock {
                calls += 1
                continuations[calls] = continuation
            }
        }
    }

    func succeed(call: Int, with summary: MenuSummary) {
        let continuation = lock.withLock {
            continuations.removeValue(forKey: call)
        }
        continuation?.resume(returning: summary)
    }

    func fail(call: Int, with error: BackendError) {
        let continuation = lock.withLock {
            continuations.removeValue(forKey: call)
        }
        continuation?.resume(throwing: error)
    }
}

private final class CoordinatorSummaryFetcherFactory: @unchecked Sendable {
    private let lock = NSLock()
    private var fetchers: [any MenuSummaryFetching]

    init(fetchers: [any MenuSummaryFetching]) {
        self.fetchers = fetchers
    }

    func next() -> any MenuSummaryFetching {
        lock.withLock { fetchers.removeFirst() }
    }
}

private func coordinatorSummary(percent: Double) -> MenuSummary {
    MenuSummary(
        usage: .init(state: .fresh, highestPercent: percent),
        sync: .init(state: .fresh, attentionCount: 0),
        observedAt: Date(timeIntervalSince1970: 1_800_000_000)
    )
}

@MainActor
private func eventually(
    timeout: Duration = .seconds(2),
    _ condition: () -> Bool
) async throws {
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: timeout)
    while !condition() {
        guard clock.now < deadline else {
            throw CoordinatorTestTimeout()
        }
        await Task.yield()
    }
}

@MainActor
private func eventuallyAsync(
    timeout: Duration = .seconds(2),
    _ condition: () async -> Bool
) async throws {
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: timeout)
    while !(await condition()) {
        guard clock.now < deadline else {
            throw CoordinatorTestTimeout()
        }
        await Task.yield()
    }
}

private struct CoordinatorTestTimeout: Error {}
