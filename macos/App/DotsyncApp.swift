import AppKit
import DotsyncKit
import SwiftUI

@main
struct DotsyncApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AccountsModel()

    var body: some Scene {
        // A single Window: the app quits when it closes (Apple's Window docs),
        // and a dotsync:// link opens it again (scene routing for external events).
        Window("dotsync", id: "main") {
            AccountsWindow(model: model)
                .onOpenURL { model.handle($0) }
        }
        .windowResizability(.contentMinSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
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

    /// Closing the window quits the app, often while the refresh it started
    /// on opening still runs. Quitting then would leave the widget on 조회 중…
    /// and drop the result, so: stay, unseen, until it is done (30 s at most),
    /// then quit again. Not `.terminateLater` — AppKit stops serving the main
    /// queue until the reply, which hung the app and every widget button.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard RefreshGate.shared.isRunning else { return .terminateNow }
        for window in sender.windows { window.orderOut(nil) }
        Task {
            await RefreshGate.shared.settle(within: .seconds(30))
            NSApp.terminate(nil)
        }
        return .terminateCancel
    }
}
