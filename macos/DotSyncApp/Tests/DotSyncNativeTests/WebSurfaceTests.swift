import AppKit
import Foundation
import SwiftUI
import WebKit
import XCTest
@testable import DotSyncApp
@testable import DotSyncNative

@MainActor
final class WebSurfaceTests: XCTestCase {
    private let token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    func testPolicyAllowsOnlyGeneratedLaunchAndQueryFreeRootsForTopLevelNavigation() throws {
        let origin = try makeOrigin()
        let policy = WebNavigationPolicy(origin: origin)
        let manager = try origin.launchURL(surface: .manager, destination: .settings)
        let popover = try origin.launchURL(surface: .popover, destination: .overview)

        for url in [manager, popover, URL(string: "http://127.0.0.1:49152")!, URL(string: "http://127.0.0.1:49152/")!] {
            XCTAssertEqual(
                policy.navigationAction(
                    url: url,
                    isMainFrame: true,
                    opensNewWindow: false,
                    shouldDownload: false
                ),
                .allow
            )
            XCTAssertEqual(
                policy.navigationResponse(
                    url: url,
                    isMainFrame: true,
                    canShowMIMEType: true
                ),
                .allow
            )
        }
    }

    func testPolicyRejectsAssetAndAPIPathsAsTopLevelButLeavesSameOriginResourcesAvailable() throws {
        let policy = WebNavigationPolicy(origin: try makeOrigin())
        let assets = [
            URL(string: "http://127.0.0.1:49152/styles.css")!,
            URL(string: "http://127.0.0.1:49152/app.mjs")!,
            URL(string: "http://127.0.0.1:49152/api/menu-summary")!,
        ]

        for url in assets {
            XCTAssertEqual(
                policy.navigationAction(
                    url: url,
                    isMainFrame: true,
                    opensNewWindow: false,
                    shouldDownload: false
                ),
                .cancel
            )
            XCTAssertTrue(policy.allowsSubresource(url))
        }
        XCTAssertFalse(
            policy.allowsSubresource(URL(string: "https://example.test/app.mjs")!)
        )
    }

    func testPolicyRejectsEveryOtherTopLevelOriginAndScheme() throws {
        let policy = WebNavigationPolicy(origin: try makeOrigin())
        let rejected = [
            "http://localhost:49152/",
            "http://127.0.0.1:49153/",
            "http://[::1]:49152/",
            "https://127.0.0.1:49152/",
            "file:///tmp/index.html",
            "data:text/html,hello",
            "javascript:void(0)",
            "blob:http://127.0.0.1:49152/value",
            "dotsync://manager",
            "http://user@127.0.0.1:49152/",
            "http://example.test/",
        ]

        for source in rejected {
            let url = try XCTUnwrap(URL(string: source), source)
            XCTAssertEqual(
                policy.navigationAction(
                    url: url,
                    isMainFrame: true,
                    opensNewWindow: false,
                    shouldDownload: false
                ),
                .cancel,
                source
            )
            XCTAssertEqual(
                policy.navigationResponse(
                    url: url,
                    isMainFrame: true,
                    canShowMIMEType: true
                ),
                .cancel,
                source
            )
        }
    }

    func testNewWindowsDownloadsAndForeignChallengesAreCancelled() throws {
        let policy = WebNavigationPolicy(origin: try makeOrigin())
        let root = URL(string: "http://127.0.0.1:49152/")!

        XCTAssertEqual(
            policy.navigationAction(
                url: root,
                isMainFrame: true,
                opensNewWindow: true,
                shouldDownload: false
            ),
            .cancel
        )
        XCTAssertFalse(policy.shouldCreateNewWebView(for: root))
        XCTAssertEqual(
            policy.navigationAction(
                url: root,
                isMainFrame: true,
                opensNewWindow: false,
                shouldDownload: true
            ),
            .cancel
        )
        XCTAssertEqual(
            policy.navigationResponse(
                url: root,
                isMainFrame: true,
                canShowMIMEType: false
            ),
            .cancel
        )
        XCTAssertEqual(
            policy.authenticationDisposition(
                scheme: "http",
                host: "127.0.0.1",
                port: 49152
            ),
            .performDefaultHandling
        )
        XCTAssertEqual(
            policy.authenticationDisposition(
                scheme: "https",
                host: "127.0.0.1",
                port: 49152
            ),
            .cancelAuthenticationChallenge
        )
        XCTAssertEqual(
            policy.authenticationDisposition(
                scheme: "http",
                host: "localhost",
                port: 49152
            ),
            .cancelAuthenticationChallenge
        )
    }

    func testBackForwardHistoryCannotLeaveAllowedRoot() throws {
        let policy = WebNavigationPolicy(origin: try makeOrigin())

        XCTAssertEqual(
            policy.navigationAction(
                url: URL(string: "http://127.0.0.1:49152/")!,
                isMainFrame: true,
                opensNewWindow: false,
                shouldDownload: false
            ),
            .allow
        )
        XCTAssertEqual(
            policy.navigationAction(
                url: URL(string: "https://example.test/back")!,
                isMainFrame: true,
                opensNewWindow: false,
                shouldDownload: false
            ),
            .cancel
        )
    }

    func testWebKitRedirectFromExactRootToExternalOriginIsCancelled() throws {
        let policy = WebNavigationPolicy(origin: try makeOrigin())
        let exactRoot = URL(string: "http://127.0.0.1:49152/")!
        let redirected = URL(string: "https://oauth-secret.example/continue")!

        XCTAssertEqual(
            policy.navigationAction(
                url: exactRoot,
                isMainFrame: true,
                opensNewWindow: false,
                shouldDownload: false
            ),
            .allow
        )
        XCTAssertEqual(
            policy.navigationResponse(
                url: redirected,
                isMainFrame: true,
                canShowMIMEType: true
            ),
            .cancel
        )
        XCTAssertFalse(policy.allowsSubresource(redirected))
    }

    func testProductionDelegateCancelsRealLoopbackRedirectBeforeExternalRequestOrBridge() async throws {
        let fixture = try RedirectNavigationFixture()
        defer { fixture.stop() }
        let commands = LockedCommandRecorder()
        let origin = try LocalOrigin(origin: fixture.origin, token: token)
        var host = Optional(NSHostingView(
            rootView: WebSurface(
                origin: origin,
                processPool: WKProcessPool(),
                surface: .manager,
                destination: .overview,
                commandHandler: { command in commands.append(command) }
            )
            .frame(width: 640, height: 480)
        ))
        host?.frame = NSRect(x: 0, y: 0, width: 640, height: 480)
        host?.layoutSubtreeIfNeeded()
        let webView = try XCTUnwrap(
            host.flatMap { descendants(of: $0, type: WKWebView.self).first }
        )

        let redirected = await Task.detached {
            fixture.waitForRedirect(timeout: .seconds(3))
        }.value
        XCTAssertTrue(redirected)
        let stopped = XCTNSPredicateExpectation(
            predicate: NSPredicate(
                block: { value, _ in
                    guard let view = value as? WKWebView else { return false }
                    return !view.isLoading
                }
            ),
            object: webView
        )
        await fulfillment(of: [stopped], timeout: 3)

        XCTAssertFalse(fixture.externalWasRequested)
        XCTAssertTrue(commands.values.isEmpty)
        host = nil
    }

    func testRealNativeHostPopoverAndManagerDismantleNeverLaunchProviderAndQuit() async throws {
        let fixture = try RealNativeSurfaceFixture()
        defer { fixture.cleanup() }
        let backend = BackendProcess(
            testOverride: fixture.wrapperURL,
            handshakeTimeout: .seconds(3)
        )
        let coordinator = AppCoordinator(
            backend: backend,
            summaryFetcherFactory: { MenuSummaryClient(origin: $0) }
        )
        await coordinator.start()
        XCTAssertNil(
            coordinator.recoveryIssue,
            "native fixture diagnostics: \(fixture.diagnostics)"
        )
        XCTAssertEqual(coordinator.summary.summary.usage.highestPercent, 58)

        var popover = Optional(NSHostingView(
            rootView: PopoverRoot(coordinator: coordinator)
                .frame(width: 360, height: 560)
        ))
        popover?.frame = NSRect(x: 0, y: 0, width: 360, height: 560)
        popover?.layoutSubtreeIfNeeded()
        var manager = Optional(NSHostingView(
            rootView: ManagerRoot(coordinator: coordinator)
                .frame(width: 920, height: 620)
        ))
        manager?.frame = NSRect(x: 0, y: 0, width: 920, height: 620)
        manager?.layoutSubtreeIfNeeded()
        let popoverWebView = try XCTUnwrap(
            popover.flatMap { descendants(of: $0, type: WKWebView.self).first }
        )
        let managerWebView = try XCTUnwrap(
            manager.flatMap { descendants(of: $0, type: WKWebView.self).first }
        )

        try await waitForDocument(in: popoverWebView)
        try await waitForDocument(in: managerWebView)
        XCTAssertEqual(popoverWebView.url?.query, nil)
        XCTAssertEqual(managerWebView.url?.query, nil)
        XCTAssertEqual(fixture.providerLaunchCount, 0)

        coordinator.managerDidClose()
        popover = nil
        manager = nil
        XCTAssertFalse(coordinator.isManagerPresented)
        XCTAssertNotNil(coordinator.session)
        XCTAssertEqual(fixture.providerLaunchCount, 0)

        await coordinator.quit()
        XCTAssertNil(coordinator.session)
        XCTAssertEqual(fixture.providerLaunchCount, 0)
        XCTAssertTrue(fixture.defaultProfilesAreUnchanged)
    }

    func testConfigurationIsEphemeralSharesPoolAndRegistersExactBridgeName() {
        let pool = WKProcessPool()
        let bridge = RecordingScriptHandler()
        let contentController = RecordingUserContentController()
        let configuration = makeWebConfiguration(
            processPool: pool,
            bridge: bridge,
            userContentController: contentController
        )

        XCTAssertFalse(configuration.websiteDataStore.isPersistent)
        XCTAssertTrue(configuration.processPool === pool)
        XCTAssertTrue(
            configuration.defaultWebpagePreferences.allowsContentJavaScript
        )
        XCTAssertTrue(configuration.userContentController === contentController)
        XCTAssertEqual(contentController.names, ["dotsyncNative"])
        XCTAssertTrue(contentController.handlers.first === bridge)
    }

    func testHandoffQueueRetainsEveryNonTrueEvaluationAndAcknowledgesInSequenceOrder() {
        let queue = ManagerHandoffDispatchQueue()
        let apply = ManagerSyncHandoff(sequence: 1, direction: .apply)
        let backup = ManagerSyncHandoff(sequence: 2, direction: .backup)

        queue.merge([backup, apply, apply])
        for (result, error) in [
            (nil, nil),
            (false, nil),
            (1, nil),
            ("true", nil),
            (true, NSError(domain: "fixture", code: 1)),
        ] as [(Any?, Error?)] {
            queue.pageDidBecomeReady()
            let rejectedAttempt = queue.beginDispatch()
            XCTAssertEqual(rejectedAttempt?.handoff, apply)
            XCTAssertNil(
                rejectedAttempt.flatMap {
                    queue.completeEvaluation(
                        $0,
                        acknowledged: exactJavaScriptTrue(
                            result,
                            error: error
                        )
                    )
                }
            )
            XCTAssertEqual(queue.pendingHandoffs, [apply, backup])
            XCTAssertNil(queue.beginDispatch())
        }

        queue.pageDidBecomeReady()
        let applyAttempt = queue.beginDispatch()
        XCTAssertEqual(
            applyAttempt.flatMap {
                queue.completeEvaluation(
                    $0,
                    acknowledged: exactJavaScriptTrue(true, error: nil)
                )
            },
            1
        )
        XCTAssertEqual(queue.pendingHandoffs, [backup])

        let backupAttempt = queue.beginDispatch()
        XCTAssertEqual(backupAttempt?.handoff, backup)
        XCTAssertEqual(
            backupAttempt.flatMap {
                queue.completeEvaluation($0, acknowledged: true)
            },
            2
        )
        XCTAssertTrue(queue.pendingHandoffs.isEmpty)
    }

    func testHandoffQueueInvalidatesProcessAttemptWithoutDroppingOrDuplicatingIt() {
        let queue = ManagerHandoffDispatchQueue()
        let apply = ManagerSyncHandoff(sequence: 4, direction: .apply)
        let backup = ManagerSyncHandoff(sequence: 5, direction: .backup)

        queue.merge([apply, backup, apply])
        queue.pageDidBecomeReady()
        let terminatedAttempt = queue.beginDispatch()
        queue.pageDidBecomeUnavailable()

        XCTAssertNil(
            terminatedAttempt.flatMap {
                queue.completeEvaluation($0, acknowledged: true)
            }
        )
        XCTAssertEqual(queue.pendingHandoffs, [apply, backup])

        queue.pageDidBecomeReady()
        let replacementAttempt = queue.beginDispatch()
        XCTAssertEqual(replacementAttempt?.handoff, apply)
        XCTAssertNotEqual(replacementAttempt, terminatedAttempt)
        XCTAssertEqual(
            replacementAttempt.flatMap {
                queue.completeEvaluation($0, acknowledged: true)
            },
            4
        )

        queue.merge([apply, backup])
        XCTAssertEqual(queue.pendingHandoffs, [backup])
    }

    func testOnlyExactBooleanTrueIsAnEvaluationAcknowledgment() {
        XCTAssertTrue(exactJavaScriptTrue(true, error: nil))
        XCTAssertFalse(exactJavaScriptTrue(false, error: nil))
        XCTAssertFalse(exactJavaScriptTrue(nil, error: nil))
        XCTAssertFalse(exactJavaScriptTrue(1, error: nil))
        XCTAssertFalse(exactJavaScriptTrue("true", error: nil))
        XCTAssertFalse(
            exactJavaScriptTrue(
                true,
                error: NSError(domain: "fixture", code: 1)
            )
        )
    }

    func testNestedBridgedObjectiveCBridgeMessagesFailClosed() throws {
        XCTAssertEqual(
            try AppBridge.decode(
                NSDictionary(
                    object: NSString(string: "quit_app"),
                    forKey: "action" as NSString
                )
            ),
            .quitApp
        )
        let nestedBodies: [Any] = [
            NSDictionary(
                dictionary: [
                    "action": NSString(string: "open_manager"),
                    "destination": NSDictionary(
                        object: NSString(string: "accounts"),
                        forKey: "value" as NSString
                    ),
                ]
            ),
            NSDictionary(
                dictionary: [
                    "action": NSArray(object: NSString(string: "quit_app")),
                ]
            ),
            NSDictionary(
                dictionary: [
                    "action": NSString(string: "refresh_summary"),
                    "nested": NSDictionary(
                        object: NSNumber(value: true),
                        forKey: "enabled" as NSString
                    ),
                ]
            ),
        ]

        for body in nestedBodies {
            XCTAssertThrowsError(try AppBridge.decode(body)) { error in
                XCTAssertEqual(error as? BackendError, .backendProtocolError)
            }
        }
    }

    func testMalformedMenuSummaryRetainsUnknownAndNeverDisplaysZero() throws {
        let model = MenuSummaryModel()
        let known = try MenuSummary.decode(
            Data(
                #"{"usage":{"state":"fresh","highest_percent":64.0},"sync":{"state":"fresh","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#
                    .utf8
            )
        )
        model.accept(known)

        model.acceptMalformedResponse()
        model.acceptMalformedResponse()

        XCTAssertEqual(model.summary, .unknown)
        XCTAssertEqual(model.menuTitle, "DotSync · —")
        XCTAssertFalse(model.menuTitle.contains("0%"))
    }

    func testRepeatedRetryAfterProtocolFailuresEventuallyOwnsOneSession() async {
        let backend = RepeatedProtocolFailureBackend(failures: 2)
        let coordinator = AppCoordinator(backend: backend)

        await coordinator.start()
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        await coordinator.retry()
        XCTAssertEqual(coordinator.recoveryIssue, .backendUnavailable)
        await coordinator.retry()

        XCTAssertNil(coordinator.recoveryIssue)
        XCTAssertNotNil(coordinator.session)
        let startCount = await backend.startCount
        let stopCount = await backend.stopCount
        XCTAssertEqual(startCount, 3)
        XCTAssertEqual(stopCount, 2)
    }

    func testSummaryPollingDuringQuitStopsBeforeBackendOwnershipIsReleased() async {
        let backend = QuitBarrierBackend()
        let fetcher = CountingWebSummaryFetcher()
        let coordinator = AppCoordinator(
            backend: backend,
            summaryFetcherFactory: { _ in fetcher }
        )
        await coordinator.start()
        let countBeforeQuit = await fetcher.count
        let quit = Task { @MainActor in await coordinator.quit() }
        await backend.waitUntilStopEntered()

        coordinator.setActive(false)
        coordinator.setActive(true)
        await coordinator.pollSummaryIfDue()

        let countDuringQuit = await fetcher.count
        XCTAssertEqual(countDuringQuit, countBeforeQuit)
        XCTAssertNotNil(coordinator.session)
        XCTAssertFalse(coordinator.isTerminated)
        await backend.releaseStop()
        await quit.value
        XCTAssertNil(coordinator.session)
        XCTAssertTrue(coordinator.isTerminated)
    }

    func testReceiverReadinessStopsAfterExactlyThreeNonTrueProbes() {
        let readiness = ManagerListenerReadiness(maximumProbeAttempts: 3)
        readiness.pageDidBecomeUnavailable()
        readiness.pageDidFinish()

        let first = readiness.beginProbe()
        XCTAssertEqual(
            first.flatMap {
                readiness.completeProbe($0, receiverAvailable: false)
            },
            .retry
        )
        let second = readiness.beginProbe()
        XCTAssertEqual(
            second.flatMap {
                readiness.completeProbe($0, receiverAvailable: false)
            },
            .retry
        )
        let third = readiness.beginProbe()
        XCTAssertEqual(
            third.flatMap {
                readiness.completeProbe($0, receiverAvailable: false)
            },
            .exhausted
        )

        XCTAssertNil(readiness.beginProbe())
        XCTAssertFalse(readiness.isReady)
    }

    func testReplacementPageRejectsStaleProbeAndDeliversRetainedHandoffAfterExactTrueProbe() {
        let readiness = ManagerListenerReadiness(maximumProbeAttempts: 3)
        let queue = ManagerHandoffDispatchQueue()
        let apply = ManagerSyncHandoff(sequence: 11, direction: .apply)
        queue.merge([apply])

        readiness.pageDidBecomeUnavailable()
        readiness.pageDidFinish()
        let oldPageProbe = readiness.beginProbe()
        readiness.pageDidBecomeUnavailable()
        queue.pageDidBecomeUnavailable()
        readiness.pageDidFinish()

        XCTAssertEqual(
            oldPageProbe.flatMap {
                readiness.completeProbe($0, receiverAvailable: true)
            },
            .ignored
        )
        XCTAssertEqual(queue.pendingHandoffs, [apply])
        XCTAssertNil(queue.beginDispatch())

        let replacementProbe = readiness.beginProbe()
        XCTAssertEqual(
            replacementProbe.flatMap {
                readiness.completeProbe($0, receiverAvailable: true)
            },
            .ready
        )
        XCTAssertTrue(readiness.isReady)
        queue.pageDidBecomeReady()
        XCTAssertEqual(queue.beginDispatch()?.handoff, apply)
    }

    private func makeOrigin() throws -> LocalOrigin {
        try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
    }

    private func waitForDocument(in webView: WKWebView) async throws {
        let stopped = XCTNSPredicateExpectation(
            predicate: NSPredicate(
                block: { value, _ in
                    guard let view = value as? WKWebView else { return false }
                    return view.url != nil && !view.isLoading
                }
            ),
            object: webView
        )
        await fulfillment(of: [stopped], timeout: 5)
        let state = try await webView.evaluateJavaScript("document.readyState")
        XCTAssertEqual(state as? String, "complete")
    }
}

private final class LockedCommandRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var commands: [NativeCommand] = []

    var values: [NativeCommand] {
        lock.withLock { commands }
    }

    func append(_ command: NativeCommand) {
        lock.withLock { commands.append(command) }
    }
}

private final class FixtureTranscript: @unchecked Sendable {
    private let condition = NSCondition()
    private var pending = Data()
    private var lines: [String] = []

    func append(_ data: Data) {
        condition.lock()
        pending.append(data)
        while let newline = pending.firstIndex(of: 0x0a) {
            lines.append(String(decoding: pending[..<newline], as: UTF8.self))
            pending.removeSubrange(...newline)
        }
        condition.broadcast()
        condition.unlock()
    }

    func waitForPrefix(_ prefix: String, timeout: Duration) -> String? {
        let deadline = Date().addingTimeInterval(timeout.timeInterval)
        condition.lock()
        defer { condition.unlock() }
        while true {
            if let line = lines.first(where: { $0.hasPrefix(prefix) }) {
                return line
            }
            if !condition.wait(until: deadline) { return nil }
        }
    }

    func containsPrefix(_ prefix: String) -> Bool {
        condition.lock()
        defer { condition.unlock() }
        return lines.contains { $0.hasPrefix(prefix) }
    }
}

private final class RedirectNavigationFixture: @unchecked Sendable {
    private(set) var origin = ""
    private let process = Process()
    private let control = Pipe()
    private let output = Pipe()
    private let transcript = FixtureTranscript()
    private let exited = DispatchSemaphore(value: 0)

    init() throws {
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-u", "-c", Self.script]
        process.standardInput = control
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [exited] _ in exited.signal() }
        output.fileHandleForReading.readabilityHandler = { [transcript] handle in
            let data = handle.availableData
            if !data.isEmpty { transcript.append(data) }
        }
        try process.run()
        control.fileHandleForReading.closeFile()
        output.fileHandleForWriting.closeFile()
        guard let ready = transcript.waitForPrefix("READY\t", timeout: .seconds(3))
        else {
            stop()
            throw BackendError.backendStartFailed
        }
        let fields = ready.split(separator: "\t")
        guard fields.count == 3 else {
            stop()
            throw BackendError.backendProtocolError
        }
        origin = "http://127.0.0.1:\(fields[1])"
    }

    var externalWasRequested: Bool {
        transcript.containsPrefix("EXTERNAL\t")
    }

    func waitForRedirect(timeout: Duration) -> Bool {
        transcript.waitForPrefix("REDIRECT\t", timeout: timeout) != nil
    }

    func stop() {
        output.fileHandleForReading.readabilityHandler = nil
        try? control.fileHandleForWriting.close()
        if process.isRunning,
           exited.wait(timeout: .now() + .seconds(3)) != .success
        {
            process.terminate()
            process.waitUntilExit()
        }
    }

    private static let script = #"""
import http.server
import sys
import threading

class External(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        print("EXTERNAL\t%s" % self.path, flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"external")
    def log_message(self, format, *args):
        pass

external = http.server.ThreadingHTTPServer(("127.0.0.1", 0), External)
external_port = external.server_address[1]

class Source(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        print("REDIRECT\t%s" % self.path, flush=True)
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:%d/escape" % external_port)
        self.end_headers()
    def log_message(self, format, *args):
        pass

source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
print("READY\t%d\t%d" % (source.server_address[1], external_port), flush=True)
threads = [
    threading.Thread(target=source.serve_forever),
    threading.Thread(target=external.serve_forever),
]
for thread in threads:
    thread.start()
sys.stdin.buffer.read()
source.shutdown()
external.shutdown()
for thread in threads:
    thread.join()
source.server_close()
external.server_close()
"""#
}

private struct ProfileEntry: Equatable {
    let path: String
    let permissions: Int
    let modified: Date
    let data: Data?
}

private final class RealNativeSurfaceFixture {
    let wrapperURL: URL
    private let rootURL: URL
    private let homeURL: URL
    private let providerLaunchesURL: URL
    private let diagnosticsURL: URL
    private let originalProfiles: [ProfileEntry]

    init() throws {
        let manager = FileManager.default
        let temporaryPath = manager.temporaryDirectory.path
        let canonicalTemporary = temporaryPath.hasPrefix("/var/")
            ? URL(fileURLWithPath: "/private" + temporaryPath, isDirectory: true)
            : manager.temporaryDirectory.resolvingSymlinksInPath()
        rootURL = canonicalTemporary.appendingPathComponent(
            "dotsync-real-native-surface-\(UUID().uuidString)",
            isDirectory: true
        )
        homeURL = rootURL.appendingPathComponent("home", isDirectory: true)
        let binURL = rootURL.appendingPathComponent("bin", isDirectory: true)
        try manager.createDirectory(at: homeURL, withIntermediateDirectories: true)
        try manager.createDirectory(at: binURL, withIntermediateDirectories: true)
        try Self.setPrivateDirectory(homeURL)
        try Self.seedDefaultProfiles(homeURL)

        let accountID = "11111111-1111-4111-8111-111111111111"
        let appRoot = homeURL
            .appendingPathComponent("Library/Application Support/DotSync", isDirectory: true)
        let accountHome = appRoot
            .appendingPathComponent("accounts/codex/\(accountID)/home", isDirectory: true)
        let usageRoot = appRoot
            .appendingPathComponent("usage/\(accountID)", isDirectory: true)
        try manager.createDirectory(at: accountHome, withIntermediateDirectories: true)
        try manager.createDirectory(at: usageRoot, withIntermediateDirectories: true)
        for url in [appRoot, accountHome.deletingLastPathComponent(), accountHome, usageRoot] {
            try Self.setPrivateDirectory(url)
        }
        try Self.writeJSON(
            [
                "schema_version": 1,
                "accounts": [[
                    "id": accountID,
                    "provider": "codex",
                    "label": "Cached Fixture",
                    "state": "ready",
                    "identity": [
                        "display_name": "Fixture",
                        "email": NSNull(),
                        "plan": "test",
                    ],
                    "created_at": "2026-08-21T12:00:00+00:00",
                ]],
            ],
            to: appRoot.appendingPathComponent("accounts.json")
        )
        try Self.writeJSON(
            [
                "account_id": accountID,
                "provider": "codex",
                "windows": [[
                    "name": "five_hour",
                    "limit_id": "primary",
                    "label": NSNull(),
                    "used_percent": 58.0,
                    "duration_minutes": 300,
                    "resets_at": NSNull(),
                ]],
                "observed_at": "2026-08-21T12:00:00Z",
                "source": "codex_app_server",
                "provider_version": "fixture-1",
            ],
            to: usageRoot.appendingPathComponent("snapshot.json")
        )

        providerLaunchesURL = rootURL.appendingPathComponent("provider-launches")
        diagnosticsURL = rootURL.appendingPathComponent("native-stderr")
        let providerURL = binURL.appendingPathComponent("codex")
        try Self.writeExecutable(
            """
            #!/bin/sh
            printf 'launch\\n' >> \(shellQuote(providerLaunchesURL.path))
            while IFS= read -r ignored; do :; done
            """,
            to: providerURL
        )

        var repository = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { repository.deleteLastPathComponent() }
        let python = repository.appendingPathComponent(".venv/bin/python3")
        wrapperURL = rootURL.appendingPathComponent("dotsync-native-wrapper")
        try Self.writeExecutable(
            """
            #!/bin/sh
            HOME=\(shellQuote(homeURL.path))
            PATH=\(shellQuote(binURL.path + ":/usr/bin:/bin"))
            PYTHONPATH=\(shellQuote(repository.appendingPathComponent("lib").path))
            export HOME PATH PYTHONPATH
            exec \(shellQuote(python.path)) -c 'from dotsync.ui_app import run_native_ui; raise SystemExit(run_native_ui())' "$@" 2>\(shellQuote(diagnosticsURL.path))
            """,
            to: wrapperURL
        )
        originalProfiles = try Self.profileSnapshot(homeURL)
    }

    var providerLaunchCount: Int {
        guard let data = try? Data(contentsOf: providerLaunchesURL) else { return 0 }
        return String(decoding: data, as: UTF8.self)
            .split(separator: "\n").count
    }

    var diagnostics: String {
        guard let data = try? Data(contentsOf: diagnosticsURL) else { return "<none>" }
        return String(decoding: data, as: UTF8.self)
    }

    var defaultProfilesAreUnchanged: Bool {
        (try? Self.profileSnapshot(homeURL)) == originalProfiles
    }

    func cleanup() {
        try? FileManager.default.removeItem(at: rootURL)
    }

    private static func seedDefaultProfiles(_ home: URL) throws {
        let manager = FileManager.default
        let claude = home.appendingPathComponent(".claude", isDirectory: true)
        let codex = home.appendingPathComponent(".codex", isDirectory: true)
        try manager.createDirectory(at: claude, withIntermediateDirectories: false)
        try manager.createDirectory(at: codex, withIntermediateDirectories: false)
        try setPrivateDirectory(claude)
        try setPrivateDirectory(codex)
        try writePrivate(Data("{\"fixture\":true}\n".utf8), to: claude.appendingPathComponent("settings.json"))
        try writePrivate(Data("{\"fixture\":true}\n".utf8), to: home.appendingPathComponent(".claude.json"))
        try writePrivate(Data("{\"fixture\":true}\n".utf8), to: codex.appendingPathComponent("auth.json"))
    }

    private static func profileSnapshot(_ home: URL) throws -> [ProfileEntry] {
        let manager = FileManager.default
        var urls: [URL] = []
        for name in [".claude", ".claude.json", ".codex"] {
            let root = home.appendingPathComponent(name)
            urls.append(root)
            if let enumerator = manager.enumerator(at: root, includingPropertiesForKeys: nil) {
                for case let child as URL in enumerator { urls.append(child) }
            }
        }
        return try urls.sorted { $0.path < $1.path }.map { url in
            let attributes = try manager.attributesOfItem(atPath: url.path)
            return ProfileEntry(
                path: url.path.replacingOccurrences(of: home.path, with: ""),
                permissions: attributes[.posixPermissions] as? Int ?? 0,
                modified: try XCTUnwrap(attributes[.modificationDate] as? Date),
                data: attributes[.type] as? FileAttributeType == .typeRegular
                    ? try Data(contentsOf: url)
                    : nil
            )
        }
    }

    private static func writeJSON(_ value: Any, to url: URL) throws {
        try writePrivate(try JSONSerialization.data(withJSONObject: value), to: url)
    }

    private static func writePrivate(_ data: Data, to url: URL) throws {
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: url.path
        )
    }

    private static func setPrivateDirectory(_ url: URL) throws {
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: url.path
        )
    }

    private static func writeExecutable(_ source: String, to url: URL) throws {
        try Data(source.utf8).write(to: url, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: url.path
        )
    }
}

private func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

private func descendants<T: NSView>(of root: NSView, type: T.Type) -> [T] {
    root.subviews.flatMap { view -> [T] in
        let current = (view as? T).map { [$0] } ?? []
        return current + descendants(of: view, type: type)
    }
}

private extension Duration {
    var timeInterval: TimeInterval {
        let components = self.components
        return TimeInterval(components.seconds)
            + TimeInterval(components.attoseconds) / 1_000_000_000_000_000_000
    }
}

private final class RecordingScriptHandler: NSObject, WKScriptMessageHandler {
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {}
}

private final class RecordingUserContentController: WKUserContentController {
    private(set) var names: [String] = []
    private(set) var handlers: [any WKScriptMessageHandler] = []

    override func add(
        _ scriptMessageHandler: any WKScriptMessageHandler,
        name: String
    ) {
        names.append(name)
        handlers.append(scriptMessageHandler)
    }
}

private actor RepeatedProtocolFailureBackend: BackendControlling {
    private var failures: Int
    private(set) var startCount = 0
    private(set) var stopCount = 0

    init(failures: Int) {
        self.failures = failures
    }

    func startBackend() async throws -> BackendSession {
        startCount += 1
        if failures > 0 {
            failures -= 1
            throw BackendError.backendProtocolError
        }
        return BackendSession(origin: try fixtureOrigin())
    }

    func stopBackend() async throws {
        stopCount += 1
    }
}

private actor QuitBarrierBackend: BackendControlling {
    private var stopEntered = false
    private var entryWaiters: [CheckedContinuation<Void, Never>] = []
    private var stopWaiter: CheckedContinuation<Void, Never>?

    func startBackend() async throws -> BackendSession {
        BackendSession(origin: try fixtureOrigin())
    }

    func stopBackend() async throws {
        stopEntered = true
        let waiters = entryWaiters
        entryWaiters.removeAll()
        for waiter in waiters { waiter.resume() }
        await withCheckedContinuation { continuation in
            stopWaiter = continuation
        }
    }

    func waitUntilStopEntered() async {
        if stopEntered { return }
        await withCheckedContinuation { continuation in
            entryWaiters.append(continuation)
        }
    }

    func releaseStop() {
        stopWaiter?.resume()
        stopWaiter = nil
    }
}

private actor CountingWebSummaryFetcher: MenuSummaryFetching {
    private(set) var count = 0

    func fetch() async throws -> MenuSummary {
        count += 1
        return .unknown
    }
}

private func fixtureOrigin() throws -> LocalOrigin {
    try LocalOrigin(
        origin: "http://127.0.0.1:49152",
        token: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
}
