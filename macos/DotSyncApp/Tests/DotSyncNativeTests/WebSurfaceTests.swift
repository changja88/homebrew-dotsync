import Foundation
import WebKit
import XCTest
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

    func testHandoffQueueRetainsFailedEvaluationAndAcknowledgesInSequenceOrder() {
        let queue = ManagerHandoffDispatchQueue()
        let apply = ManagerSyncHandoff(sequence: 1, direction: .apply)
        let backup = ManagerSyncHandoff(sequence: 2, direction: .backup)

        queue.merge([backup, apply])
        queue.pageDidBecomeReady()
        let firstAttempt = queue.beginDispatch()

        XCTAssertEqual(firstAttempt?.handoff, apply)
        XCTAssertNil(queue.beginDispatch())
        XCTAssertNil(
            firstAttempt.flatMap {
                queue.complete($0, evaluationSucceeded: false)
            }
        )
        XCTAssertEqual(queue.pendingHandoffs, [apply, backup])
        XCTAssertNil(queue.beginDispatch())

        queue.pageDidBecomeReady()
        let retryAttempt = queue.beginDispatch()
        XCTAssertEqual(retryAttempt?.handoff, apply)
        XCTAssertEqual(
            retryAttempt.flatMap {
                queue.complete($0, evaluationSucceeded: true)
            },
            1
        )
        XCTAssertEqual(queue.pendingHandoffs, [backup])

        let secondAttempt = queue.beginDispatch()
        XCTAssertEqual(secondAttempt?.handoff, backup)
        XCTAssertEqual(
            secondAttempt.flatMap {
                queue.complete($0, evaluationSucceeded: true)
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
                queue.complete($0, evaluationSucceeded: true)
            }
        )
        XCTAssertEqual(queue.pendingHandoffs, [apply, backup])

        queue.pageDidBecomeReady()
        let replacementAttempt = queue.beginDispatch()
        XCTAssertEqual(replacementAttempt?.handoff, apply)
        XCTAssertNotEqual(replacementAttempt, terminatedAttempt)
        XCTAssertEqual(
            replacementAttempt.flatMap {
                queue.complete($0, evaluationSucceeded: true)
            },
            4
        )

        queue.merge([apply, backup])
        XCTAssertEqual(queue.pendingHandoffs, [backup])
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
