import AppKit
import DotsyncKit
import SwiftUI

@main
struct DotsyncApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        // A single Window: the app quits when it closes (Apple's Window docs),
        // and a dotsync:// link opens it again (scene routing for external events).
        Window("dotsync", id: "main") {
            RootView(accounts: delegate.model, settings: delegate.settings)
        }
        .windowStyle(.hiddenTitleBar)
        // With no title bar left, the window moves by its background.
        .windowBackgroundDragBehavior(.enabled)
        .defaultSize(width: 1080, height: 640)
        .windowResizability(.contentMinSize)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Every dotsync command the window runs, one at a time: account and
    /// sync commands alike.
    let queue: CommandQueue
    /// Here, not in the App, so quitting can see what it runs.
    let model: AccountsModel
    let settings: SettingsModel
    /// Set when quitting starts to wait for work in flight.
    private var quitBy: ContinuousClock.Instant?

    /// dotsync still at work for the window, a widget button, or a refresh.
    private var isWorking: Bool {
        queue.running != nil || IntentWork.running > 0 || RefreshGate.shared.isRunning
    }

    override init() {
        let queue = CommandQueue()
        self.queue = queue
        model = AccountsModel(queue: queue)
        settings = SettingsModel(queue: queue)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppHooks.open = { url in _ = NSWorkspace.shared.open(url) }
        // A widget button may launch the app with no window (spike Q1). Once
        // every button's work is done, don't stay behind.
        AppHooks.afterIntent = {
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2))
                if IntentWork.running == 0 && !NSApp.windows.contains(where: \.isVisible) {
                    NSApp.terminate(nil)
                }
            }
        }
    }

    /// Closing the window quits the app, often while something it started
    /// still runs: the refresh on opening, a switch, a widget button's work.
    /// Quitting then drops the result — the widget stays on 조회 중…, the card
    /// on the old account — so: stop a login waiting for the browser, stay
    /// unseen until the rest is done (30 s at most), then quit again. Not
    /// `.terminateLater` — AppKit stops serving the main queue until the
    /// reply, which hung the app and every widget button.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let quitBy {
            return isWorking && ContinuousClock.now < quitBy ? .terminateCancel : .terminateNow
        }
        guard isWorking else { return .terminateNow }
        quitBy = ContinuousClock.now + .seconds(30)
        model.cancelLogin()
        for window in sender.windows { window.orderOut(nil) }
        Task {
            while isWorking, let quitBy, ContinuousClock.now < quitBy {
                try? await Task.sleep(for: .milliseconds(200))
            }
            // A refresh still going is stopped, which puts usage.json back.
            await RefreshGate.shared.settle(within: .zero)
            NSApp.terminate(nil)
        }
        return .terminateCancel
    }
}
