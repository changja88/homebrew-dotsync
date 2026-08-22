import Foundation
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
