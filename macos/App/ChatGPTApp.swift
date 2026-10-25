import AppKit
import DotsyncKit

private let chatGPTBundleID = "com.openai.codex"

/// ChatGPT.app (the Codex desktop app) as the cleanup unit drives it. It only
/// asks: `terminate()` is the polite request, never a force quit. Nothing here
/// is exercised by tests; the real run is the by-hand check with the user.
@MainActor
final class ChatGPTApp: ChatGPTControl {
    /// The instances this adapter asked to quit, and when. One of them that is
    /// not yet `isTerminated` is a ChatGPT that is still quitting, until
    /// `ChatGPTQuitRules` says it declined.
    private var asked: [(app: NSRunningApplication, sentAt: Date)] = []
    private let now: () -> Date

    init(now: @escaping () -> Date = Date.init) {
        self.now = now
    }

    private var live: [NSRunningApplication] {
        NSRunningApplication.runningApplications(withBundleIdentifier: chatGPTBundleID).filter { !$0.isTerminated }
    }

    var isRunning: Bool { !live.isEmpty }

    func terminate() -> Bool {
        let instances = live
        guard !instances.isEmpty else { return false }
        let sentAt = now()
        asked = instances.map { ($0, sentAt) }
        // Every instance is asked, even after one refuses.
        let answers = instances.map { $0.terminate() }
        return !answers.contains(false)
    }

    /// Polls `isTerminated` on the instances `terminate()` asked (or, with
    /// none asked, the ones running now) and gives up at `timeout`.
    func waitUntilTerminated(timeout: Duration) async -> Bool {
        let waiting = asked.isEmpty ? live : asked.map(\.app)
        let deadline = ContinuousClock.now + timeout
        while !waiting.allSatisfy(\.isTerminated) {
            if ContinuousClock.now >= deadline || Task.isCancelled { return false }
            try? await Task.sleep(for: .milliseconds(100))
        }
        return true
    }

    func relaunch() async -> Bool {
        // One that has outlived the quit request declined it: it is open, so
        // it is no longer "quitting".
        asked.removeAll { !$0.app.isTerminated && ChatGPTQuitRules.declined(askedAt: $0.sentAt, now: now()) }
        let running = live
        let quitting = running.filter { app in asked.contains { $0.app.processIdentifier == app.processIdentifier } }
        // A ChatGPT that is open and not quitting is the goal already: do not
        // activate it or take the focus.
        if running.count > quitting.count { return true }
        // One still quitting does not count. Give it a moment; if it stays,
        // report false and the unit tries again in 5 s.
        if !quitting.isEmpty {
            guard await waitUntilTerminated(timeout: .seconds(4)) else { return false }
        }
        asked = []
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: chatGPTBundleID) else { return false }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        do {
            _ = try await NSWorkspace.shared.openApplication(at: url, configuration: configuration)
            return true
        } catch {
            return false
        }
    }

    /// Registered when the stream is made and unbounded, so a launch before
    /// anyone reads is kept. The observer goes when the stream ends.
    func launches() -> AsyncStream<Void> {
        AsyncStream(Void.self, bufferingPolicy: .unbounded) { continuation in
            let center = NSWorkspace.shared.notificationCenter
            let observer = ObserverToken(center.addObserver(
                forName: NSWorkspace.didLaunchApplicationNotification, object: nil, queue: nil
            ) { note in
                let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
                if app?.bundleIdentifier == chatGPTBundleID { continuation.yield() }
            })
            continuation.onTermination = { _ in center.removeObserver(observer.value) }
        }
    }
}

/// NotificationCenter hands out its observer as a plain object; it is only
/// ever passed back to `removeObserver`.
private final class ObserverToken: @unchecked Sendable {
    let value: NSObjectProtocol
    init(_ value: NSObjectProtocol) { self.value = value }
}
