import Foundation
import SwiftUI
import WebKit

public struct ManagerSyncHandoff: Equatable, Sendable {
    public let sequence: UInt64
    public let direction: ManagerSyncDirection

    public init(sequence: UInt64, direction: ManagerSyncDirection) {
        self.sequence = sequence
        self.direction = direction
    }
}

struct ManagerHandoffDispatchAttempt: Equatable {
    let identifier: UInt64
    let handoff: ManagerSyncHandoff
}

@MainActor
final class ManagerHandoffDispatchQueue {
    private(set) var pendingHandoffs: [ManagerSyncHandoff] = []
    private var activeAttempt: ManagerHandoffDispatchAttempt?
    private var activeEvaluationSucceeded = false
    private var activeReceiptAcknowledged = false
    private var nextAttemptIdentifier: UInt64 = 0
    private var highestAcknowledgedSequence: UInt64 = 0
    private var isPageReady = false

    func merge(_ handoffs: [ManagerSyncHandoff]) {
        var knownSequences = Set(pendingHandoffs.map(\.sequence))
        for handoff in handoffs
        where handoff.sequence > highestAcknowledgedSequence
            && knownSequences.insert(handoff.sequence).inserted
        {
            pendingHandoffs.append(handoff)
        }
        pendingHandoffs.sort { $0.sequence < $1.sequence }
    }

    func pageDidBecomeReady() {
        isPageReady = true
    }

    func pageDidBecomeUnavailable() {
        isPageReady = false
        clearActiveAttempt()
    }

    func beginDispatch() -> ManagerHandoffDispatchAttempt? {
        guard isPageReady,
              activeAttempt == nil,
              let handoff = pendingHandoffs.first
        else { return nil }
        nextAttemptIdentifier &+= 1
        let attempt = ManagerHandoffDispatchAttempt(
            identifier: nextAttemptIdentifier,
            handoff: handoff
        )
        activeAttempt = attempt
        activeEvaluationSucceeded = false
        activeReceiptAcknowledged = false
        return attempt
    }

    func completeEvaluation(
        _ attempt: ManagerHandoffDispatchAttempt,
        succeeded: Bool
    ) -> UInt64? {
        guard activeAttempt == attempt else { return nil }
        guard succeeded else {
            clearActiveAttempt()
            isPageReady = false
            return nil
        }
        activeEvaluationSucceeded = true
        return finishAcknowledgedAttemptIfReady()
    }

    func acknowledgeReceipt() -> UInt64? {
        guard activeAttempt != nil else { return nil }
        activeReceiptAcknowledged = true
        return finishAcknowledgedAttemptIfReady()
    }

    private func finishAcknowledgedAttemptIfReady() -> UInt64? {
        guard activeEvaluationSucceeded,
              activeReceiptAcknowledged,
              let attempt = activeAttempt,
              pendingHandoffs.first == attempt.handoff
        else { return nil }
        pendingHandoffs.removeFirst()
        highestAcknowledgedSequence = attempt.handoff.sequence
        clearActiveAttempt()
        return attempt.handoff.sequence
    }

    private func clearActiveAttempt() {
        activeAttempt = nil
        activeEvaluationSucceeded = false
        activeReceiptAcknowledged = false
    }
}

struct ManagerListenerReadinessProbe: Equatable {
    let pageGeneration: UInt64
    let attemptNumber: Int
}

enum ManagerListenerReadinessProbeCompletion: Equatable {
    case ignored
    case retry
    case exhausted
    case failed
    case ready
}

@MainActor
final class ManagerListenerReadiness {
    private let maximumProbeAttempts: Int
    private var pageGeneration: UInt64 = 0
    private var probeAttempts = 0
    private var activeProbe: ManagerListenerReadinessProbe?
    private var documentDidFinish = false
    private var listenerAcknowledged = false

    init(maximumProbeAttempts: Int) {
        precondition(maximumProbeAttempts > 0)
        self.maximumProbeAttempts = maximumProbeAttempts
    }

    var isDocumentReady: Bool {
        documentDidFinish
    }

    var isReady: Bool {
        documentDidFinish && listenerAcknowledged
    }

    func pageDidBecomeUnavailable() {
        pageGeneration &+= 1
        probeAttempts = 0
        activeProbe = nil
        documentDidFinish = false
        listenerAcknowledged = false
    }

    func pageDidFinish() {
        documentDidFinish = true
    }

    func listenerDidAcknowledge() {
        listenerAcknowledged = true
        activeProbe = nil
    }

    func beginProbe() -> ManagerListenerReadinessProbe? {
        guard documentDidFinish,
              !listenerAcknowledged,
              activeProbe == nil,
              probeAttempts < maximumProbeAttempts
        else { return nil }
        probeAttempts += 1
        let probe = ManagerListenerReadinessProbe(
            pageGeneration: pageGeneration,
            attemptNumber: probeAttempts
        )
        activeProbe = probe
        return probe
    }

    func completeProbe(
        _ probe: ManagerListenerReadinessProbe,
        evaluationSucceeded: Bool
    ) -> ManagerListenerReadinessProbeCompletion {
        guard activeProbe == probe else { return .ignored }
        activeProbe = nil
        guard evaluationSucceeded else { return .failed }
        if listenerAcknowledged {
            return .ready
        }
        return probeAttempts == maximumProbeAttempts
            ? .exhausted
            : .retry
    }
}

@MainActor
public final class WebNavigationPolicy {
    private let origin: LocalOrigin

    public init(origin: LocalOrigin) {
        self.origin = origin
    }

    public func navigationAction(
        url: URL?,
        isMainFrame: Bool,
        opensNewWindow: Bool,
        shouldDownload: Bool
    ) -> WKNavigationActionPolicy {
        guard isMainFrame,
              !opensNewWindow,
              !shouldDownload,
              let url,
              origin.accepts(url)
        else { return .cancel }
        return .allow
    }

    public func navigationResponse(
        url: URL?,
        isMainFrame: Bool,
        canShowMIMEType: Bool
    ) -> WKNavigationResponsePolicy {
        guard isMainFrame,
              canShowMIMEType,
              let url,
              origin.accepts(url)
        else { return .cancel }
        return .allow
    }

    public func allowsSubresource(_ url: URL) -> Bool {
        guard let expectedPort = origin.baseURL.port,
              let components = URLComponents(
                url: url,
                resolvingAgainstBaseURL: false
              )
        else { return false }
        return components.scheme == "http"
            && components.host == "127.0.0.1"
            && components.port == expectedPort
            && components.user == nil
            && components.password == nil
    }

    public func shouldCreateNewWebView(for url: URL?) -> Bool {
        if let url {
            _ = origin.accepts(url)
        }
        return false
    }

    public func authenticationDisposition(
        scheme: String,
        host: String,
        port: Int
    ) -> URLSession.AuthChallengeDisposition {
        guard scheme == "http",
              host == "127.0.0.1",
              port == origin.baseURL.port
        else { return .cancelAuthenticationChallenge }
        return .performDefaultHandling
    }
}

@MainActor
public func makeWebConfiguration(
    processPool: WKProcessPool,
    bridge: WKScriptMessageHandler
) -> WKWebViewConfiguration {
    makeWebConfiguration(
        processPool: processPool,
        bridge: bridge,
        userContentController: WKUserContentController()
    )
}

@MainActor
func makeWebConfiguration(
    processPool: WKProcessPool,
    bridge: WKScriptMessageHandler,
    userContentController: WKUserContentController
) -> WKWebViewConfiguration {
    let configuration = WKWebViewConfiguration()
    configuration.websiteDataStore = .nonPersistent()
    configuration.processPool = processPool
    configuration.defaultWebpagePreferences.allowsContentJavaScript = true
    configuration.userContentController = userContentController
    userContentController.add(
        bridge,
        name: WebSurface.bridgeName
    )
    return configuration
}

public struct WebSurface: NSViewRepresentable {
    fileprivate static let bridgeName = "dotsyncNative"

    private let origin: LocalOrigin
    private let processPool: WKProcessPool
    private let surface: LocalOrigin.Surface
    private let destination: LocalOrigin.Destination
    private let handoffs: [ManagerSyncHandoff]
    private let handoffAcknowledged: @MainActor (UInt64) -> Void
    private let commandHandler: @MainActor (NativeCommand) -> Void

    public init(
        origin: LocalOrigin,
        processPool: WKProcessPool,
        surface: LocalOrigin.Surface,
        destination: LocalOrigin.Destination = .overview,
        handoffs: [ManagerSyncHandoff] = [],
        handoffAcknowledged: @escaping @MainActor (UInt64) -> Void = { _ in },
        commandHandler: @escaping @MainActor (NativeCommand) -> Void
    ) {
        self.origin = origin
        self.processPool = processPool
        self.surface = surface
        self.destination = destination
        self.handoffs = handoffs
        self.handoffAcknowledged = handoffAcknowledged
        self.commandHandler = commandHandler
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(
            origin: origin,
            handoffAcknowledged: handoffAcknowledged,
            commandHandler: commandHandler
        )
    }

    public func makeNSView(context: Context) -> WKWebView {
        let configuration = makeWebConfiguration(
            processPool: processPool,
            bridge: context.coordinator
        )
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        context.coordinator.attach(webView)
        context.coordinator.update(
            surface: surface,
            destination: destination,
            handoffs: handoffs,
            handoffAcknowledged: handoffAcknowledged,
            commandHandler: commandHandler
        )
        return webView
    }

    public func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.update(
            surface: surface,
            destination: destination,
            handoffs: handoffs,
            handoffAcknowledged: handoffAcknowledged,
            commandHandler: commandHandler
        )
    }

    public static func dismantleNSView(
        _ webView: WKWebView,
        coordinator: Coordinator
    ) {
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        webView.configuration.userContentController
            .removeScriptMessageHandler(forName: bridgeName)
        coordinator.detach()
    }

    @MainActor
    public final class Coordinator: NSObject, WKNavigationDelegate,
        WKUIDelegate, WKScriptMessageHandler {
        private struct LaunchContext: Equatable {
            let surface: LocalOrigin.Surface
            let destination: LocalOrigin.Destination
        }

        private let origin: LocalOrigin
        private let policy: WebNavigationPolicy
        private weak var webView: WKWebView?
        private var launchContext: LaunchContext?
        private let listenerReadiness = ManagerListenerReadiness(
            maximumProbeAttempts: 3
        )
        private var readinessRetryTask: Task<Void, Never>?
        private var pageGeneration: UInt64 = 0
        private let handoffQueue = ManagerHandoffDispatchQueue()
        private var handoffAcknowledged: @MainActor (UInt64) -> Void
        private var commandHandler: @MainActor (NativeCommand) -> Void

        fileprivate init(
            origin: LocalOrigin,
            handoffAcknowledged: @escaping @MainActor (UInt64) -> Void,
            commandHandler: @escaping @MainActor (NativeCommand) -> Void
        ) {
            self.origin = origin
            self.policy = WebNavigationPolicy(origin: origin)
            self.handoffAcknowledged = handoffAcknowledged
            self.commandHandler = commandHandler
        }

        fileprivate func attach(_ webView: WKWebView) {
            self.webView = webView
        }

        fileprivate func detach() {
            webView = nil
            markPageUnavailable()
        }

        fileprivate func update(
            surface: LocalOrigin.Surface,
            destination: LocalOrigin.Destination,
            handoffs: [ManagerSyncHandoff],
            handoffAcknowledged: @escaping @MainActor (UInt64) -> Void,
            commandHandler: @escaping @MainActor (NativeCommand) -> Void
        ) {
            self.commandHandler = commandHandler
            self.handoffAcknowledged = handoffAcknowledged
            handoffQueue.merge(handoffs)

            let requested = LaunchContext(
                surface: surface,
                destination: destination
            )
            guard requested != launchContext else {
                dispatchPendingHandoffIfReady()
                return
            }

            launchContext = requested
            markPageUnavailable()
            guard let webView,
                  let launchURL = try? origin.launchURL(
                    surface: surface,
                    destination: destination
                  )
            else { return }
            webView.load(URLRequest(url: launchURL))
        }

        public func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard message.name == WebSurface.bridgeName,
                  message.frameInfo.isMainFrame,
                  let frameURL = message.frameInfo.request.url,
                  origin.accepts(frameURL),
                  let bridgeMessage = try? AppBridge.decodeMessage(message.body)
            else { return }
            switch bridgeMessage {
            case let .command(command):
                commandHandler(command)
            case .managerSyncListenerReady:
                guard launchContext == LaunchContext(
                    surface: .manager,
                    destination: .sync
                ) else { return }
                listenerReadiness.listenerDidAcknowledge()
                readinessRetryTask?.cancel()
                readinessRetryTask = nil
                if listenerReadiness.isReady {
                    handoffQueue.pageDidBecomeReady()
                    dispatchPendingHandoffIfReady()
                }
            case .managerSyncHandoffReceived:
                acknowledgeReceivedHandoff()
            }
        }

        public func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            decisionHandler(
                policy.navigationAction(
                    url: navigationAction.request.url,
                    isMainFrame: navigationAction.targetFrame?.isMainFrame
                        == true,
                    opensNewWindow: navigationAction.targetFrame == nil,
                    shouldDownload: navigationAction.shouldPerformDownload
                )
            )
        }

        public func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
        ) {
            decisionHandler(
                policy.navigationResponse(
                    url: navigationResponse.response.url,
                    isMainFrame: navigationResponse.isForMainFrame,
                    canShowMIMEType: navigationResponse.canShowMIMEType
                )
            )
        }

        public func webView(
            _ webView: WKWebView,
            didStartProvisionalNavigation navigation: WKNavigation!
        ) {
            markPageUnavailable()
        }

        public func webView(
            _ webView: WKWebView,
            didFinish navigation: WKNavigation!
        ) {
            guard let url = webView.url, origin.accepts(url) else {
                markPageUnavailable()
                return
            }
            listenerReadiness.pageDidFinish()
            if listenerReadiness.isReady {
                handoffQueue.pageDidBecomeReady()
            }
            dispatchPendingHandoffIfReady()
        }

        public func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation!,
            withError error: Error
        ) {
            markPageUnavailable()
        }

        public func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            markPageUnavailable()
        }

        public func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            markPageUnavailable()
        }

        public func webView(
            _ webView: WKWebView,
            didReceive challenge: URLAuthenticationChallenge,
            completionHandler: @escaping (
                URLSession.AuthChallengeDisposition,
                URLCredential?
            ) -> Void
        ) {
            let protectionSpace = challenge.protectionSpace
            let disposition = policy.authenticationDisposition(
                scheme: protectionSpace.protocol ?? "",
                host: protectionSpace.host,
                port: protectionSpace.port
            )
            completionHandler(disposition, nil)
        }

        public func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            _ = policy.shouldCreateNewWebView(
                for: navigationAction.request.url
            )
            return nil
        }

        private func dispatchPendingHandoffIfReady() {
            guard listenerReadiness.isDocumentReady,
                  let webView,
                  launchContext == LaunchContext(
                    surface: .manager,
                    destination: .sync
                  )
            else { return }
            guard listenerReadiness.isReady else {
                probeListenerReadiness(in: webView)
                return
            }
            dispatchReadyHandoff(in: webView)
        }

        private func probeListenerReadiness(in webView: WKWebView) {
            guard let probe = listenerReadiness.beginProbe() else { return }
            let ownedPageGeneration = pageGeneration
            webView.evaluateJavaScript(
                #"window.dispatchEvent(new Event("dotsync:manager-sync-listener-probe"));"#
            ) { [weak self] _, error in
                let probeFailed = error != nil
                Task { @MainActor [weak self] in
                    guard let self,
                          self.pageGeneration == ownedPageGeneration
                    else { return }
                    switch self.listenerReadiness.completeProbe(
                        probe,
                        evaluationSucceeded: !probeFailed
                    ) {
                    case .failed:
                        self.markPageUnavailable()
                    case .retry:
                        self.scheduleReadinessProbe(
                            for: ownedPageGeneration
                        )
                    case .ready:
                        self.dispatchPendingHandoffIfReady()
                    case .ignored, .exhausted:
                        break
                    }
                }
            }
        }

        private func scheduleReadinessProbe(for ownedPageGeneration: UInt64) {
            readinessRetryTask?.cancel()
            readinessRetryTask = Task { @MainActor [weak self] in
                do {
                    try await Task.sleep(for: .milliseconds(10))
                } catch {
                    return
                }
                guard let self,
                      self.pageGeneration == ownedPageGeneration,
                      self.listenerReadiness.isDocumentReady
                else { return }
                self.readinessRetryTask = nil
                self.dispatchPendingHandoffIfReady()
            }
        }

        private func dispatchReadyHandoff(in webView: WKWebView) {
            guard let attempt = handoffQueue.beginDispatch() else { return }
            let ownedPageGeneration = pageGeneration
            webView.evaluateJavaScript(
                attempt.handoff.direction.eventJavaScript
            ) { [weak self] _, error in
                let evaluationSucceeded = error == nil
                Task { @MainActor [weak self] in
                    guard let self,
                          self.pageGeneration == ownedPageGeneration
                    else { return }
                    if let sequence = self.handoffQueue.completeEvaluation(
                        attempt,
                        succeeded: evaluationSucceeded
                    ) {
                        self.handoffAcknowledged(sequence)
                        self.dispatchPendingHandoffIfReady()
                    } else if !evaluationSucceeded {
                        self.markPageUnavailable()
                    }
                }
            }
        }

        private func acknowledgeReceivedHandoff() {
            guard listenerReadiness.isReady,
                  let sequence = handoffQueue.acknowledgeReceipt()
            else { return }
            handoffAcknowledged(sequence)
            dispatchPendingHandoffIfReady()
        }

        private func markPageUnavailable() {
            readinessRetryTask?.cancel()
            readinessRetryTask = nil
            listenerReadiness.pageDidBecomeUnavailable()
            pageGeneration &+= 1
            handoffQueue.pageDidBecomeUnavailable()
        }
    }
}
