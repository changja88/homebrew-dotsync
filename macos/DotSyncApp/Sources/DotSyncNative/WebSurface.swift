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
    private let handoff: ManagerSyncHandoff?
    private let commandHandler: @MainActor (NativeCommand) -> Void

    public init(
        origin: LocalOrigin,
        processPool: WKProcessPool,
        surface: LocalOrigin.Surface,
        destination: LocalOrigin.Destination = .overview,
        handoff: ManagerSyncHandoff? = nil,
        commandHandler: @escaping @MainActor (NativeCommand) -> Void
    ) {
        self.origin = origin
        self.processPool = processPool
        self.surface = surface
        self.destination = destination
        self.handoff = handoff
        self.commandHandler = commandHandler
    }

    public func makeCoordinator() -> Coordinator {
        Coordinator(origin: origin, commandHandler: commandHandler)
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
            handoff: handoff,
            commandHandler: commandHandler
        )
        return webView
    }

    public func updateNSView(_ webView: WKWebView, context: Context) {
        context.coordinator.update(
            surface: surface,
            destination: destination,
            handoff: handoff,
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
        private var listenerReady = false
        private var lastHandoffSequence: UInt64?
        private var pendingHandoff: ManagerSyncDirection?
        private var commandHandler: @MainActor (NativeCommand) -> Void

        fileprivate init(
            origin: LocalOrigin,
            commandHandler: @escaping @MainActor (NativeCommand) -> Void
        ) {
            self.origin = origin
            self.policy = WebNavigationPolicy(origin: origin)
            self.commandHandler = commandHandler
        }

        fileprivate func attach(_ webView: WKWebView) {
            self.webView = webView
        }

        fileprivate func detach() {
            webView = nil
            listenerReady = false
            pendingHandoff = nil
        }

        fileprivate func update(
            surface: LocalOrigin.Surface,
            destination: LocalOrigin.Destination,
            handoff: ManagerSyncHandoff?,
            commandHandler: @escaping @MainActor (NativeCommand) -> Void
        ) {
            self.commandHandler = commandHandler
            if handoff?.sequence != lastHandoffSequence {
                lastHandoffSequence = handoff?.sequence
                pendingHandoff = handoff?.direction
            }

            let requested = LaunchContext(
                surface: surface,
                destination: destination
            )
            guard requested != launchContext else {
                dispatchPendingHandoffIfReady()
                return
            }

            launchContext = requested
            listenerReady = false
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
                  let command = try? AppBridge.decode(message.body)
            else { return }
            commandHandler(command)
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
            didFinish navigation: WKNavigation!
        ) {
            guard let url = webView.url, origin.accepts(url) else {
                listenerReady = false
                return
            }
            listenerReady = true
            dispatchPendingHandoffIfReady()
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
            guard listenerReady,
                  let webView,
                  let pendingHandoff,
                  launchContext == LaunchContext(
                    surface: .manager,
                    destination: .sync
                  )
            else { return }
            self.pendingHandoff = nil
            webView.evaluateJavaScript(pendingHandoff.eventJavaScript) {
                _, _ in
            }
        }
    }
}
