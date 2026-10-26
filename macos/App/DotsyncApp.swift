import AppKit
import DotsyncKit
import SwiftUI

/// Started by `Entry`, unless launchd runs the executable in check mode.
struct DotsyncApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        // A single Window: the app quits when it closes (Apple's Window docs),
        // and a dotsync:// link opens it again (scene routing for external events).
        Window("dotsync", id: "main") {
            RootView(accounts: delegate.model, settings: delegate.settings, sessions: delegate.sessions,
                     quitting: delegate)
        }
        .windowStyle(.hiddenTitleBar)
        // With no title bar left, the window moves by its background.
        .windowBackgroundDragBehavior(.enabled)
        .defaultSize(width: 1080, height: 640)
        .windowResizability(.contentMinSize)
    }
}

@MainActor
@Observable
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// The window's dotsync commands, one at a time per menu: 사용량's account
    /// commands in one queue, 설정's sync commands in the other, so a check
    /// does not wait for a usage refresh. Where the two meet (a Claude push or
    /// pull and an account switch) the CLI's accounts lock takes turns.
    let accountQueue: CommandQueue
    let syncQueue: CommandQueue
    /// Here, not in the App, so quitting can see what it runs.
    let model: AccountsModel
    let settings: SettingsModel
    /// One queue per tool: two commands for the same tool never overlap, while
    /// Claude Code and Codex may run together.
    let claudeSessionQueue: CommandQueue
    let codexSessionQueue: CommandQueue
    let sessions: SessionsModel
    /// Set when quitting starts to wait unseen for work in flight.
    @ObservationIgnored private var quitBy: ContinuousClock.Instant?
    /// Quitting waits in the window for long work, then quits.
    private(set) var quitWhenDone = false
    @ObservationIgnored private var windowWait: Task<Void, Never>?
    /// Opens the window again: closing it is how most quits start. RootView
    /// sets it, since opening a window is SwiftUI's.
    @ObservationIgnored var showWindow: (() -> Void)?
    /// When the last power-off began (willPowerOff, or a logout's quit).
    @ObservationIgnored private var powerOffStarted: ContinuousClock.Instant?
    /// From that moment until the app ends, or for 60 s: a power-off that has
    /// not ended the app by then was cancelled, and quitting is ordinary
    /// again. Read through the rule each time, so it never lapses inside the
    /// reply's 20 s wait.
    private var poweringOff: Bool { QuitRules.stillPoweringOff(since: powerOffStarted, now: .now) }
    /// Left once the power-off stop has returned, by `powerOffDeadline`.
    @ObservationIgnored private let powerOffStop = DispatchGroup()
    @ObservationIgnored private var powerOffDeadline = DispatchTime.distantFuture

    /// Work that quitting must not cut off halfway: a session delete or
    /// recover, the whole Codex cleanup unit, a sync apply. Its value is the
    /// window's line while quitting waits for it.
    var longWork: String? {
        QuitRules.longWork(sessionWork: sessions.isWorking, applying: settings.applying)
    }

    /// The line at the top of the window while quitting waits; nil otherwise.
    var quitNotice: String? {
        guard quitWhenDone, let longWork else { return nil }
        return QuitRules.notice(longWork, runs: sessions.runningWork.map { ($0.label, $0.progress) })
    }

    /// dotsync still at work for the window, a widget button, or a refresh.
    private var isWorking: Bool {
        accountQueue.running != nil || syncQueue.running != nil || IntentWork.running > 0
            || RefreshGate.shared.isRunning || longWork != nil
            || claudeSessionQueue.running != nil || codexSessionQueue.running != nil
    }

    override init() {
        accountQueue = CommandQueue()
        syncQueue = CommandQueue()
        model = AccountsModel(queue: accountQueue)
        settings = SettingsModel(queue: syncQueue)
        claudeSessionQueue = CommandQueue()
        codexSessionQueue = CommandQueue()
        sessions = SessionsModel(claudeQueue: claudeSessionQueue, codexQueue: codexSessionQueue)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppHooks.open = { url in _ = NSWorkspace.shared.open(url) }
        offerRelaunchIfNeeded()
        // A widget button may launch the app with no window (spike Q1). Once
        // every button's work is done, and nothing else runs, don't stay behind.
        AppHooks.afterIntent = { [weak self] in
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2))
                guard let self, !self.isWorking, !NSApp.windows.contains(where: \.isVisible) else { return }
                NSApp.terminate(nil)
            }
        }
        // Only NSWorkspace's own center posts it.
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willPowerOffNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.powerOff() }
        }
    }

    /// A cleanup the app did not finish (a crash, a logout) can leave ChatGPT
    /// quit by dotsync. Ask once whether to open it again.
    private func offerRelaunchIfNeeded() {
        guard sessions.shouldOfferRelaunch else { return }
        let alert = NSAlert()
        alert.messageText = "dotsync가 끈 ChatGPT를 다시 켤까요?"
        alert.addButton(withTitle: "다시 켜기")
        alert.addButton(withTitle: "그대로 두기")
        Task { @MainActor in
            if alert.runModal() == .alertFirstButtonReturn {
                // A failure keeps the flag, so the next launch asks again.
                _ = await sessions.relaunchChatGPT()
            } else {
                sessions.declineRelaunch()
            }
        }
    }

    /// Closing the window quits the app, often while something it started
    /// still runs. Long work cut off halfway leaves sessions half deleted or
    /// an apply half done, so the window stays until it ends (`QuitRules`).
    /// Other work — the refresh on opening, a switch, a widget button's work —
    /// would only lose its result (the widget stays on 조회 중…, the card on
    /// the old account), so: stop a login waiting for the browser, stay unseen
    /// until the rest is done (30 s at most), then quit again. A logout or
    /// restart quits once the long runs have stopped. Never `.terminateLater`
    /// — AppKit stops serving the main queue until the reply, which hung the
    /// app and every widget button.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Should the quit for a logout come before willPowerOff, waiting in
        // the window would cancel the logout: start the same stop now.
        if quitIsPowerOff { powerOff() }
        switch QuitRules.reply(longWork: longWork, isWorking: isWorking, quitBy: quitBy, now: .now,
                               poweringOff: poweringOff) {
        case .now:
            // Blocking here is the only way to give the CLI its safe-point
            // exit before the reply; the stop runs off the main thread.
            if poweringOff { _ = powerOffStop.wait(timeout: powerOffDeadline) }
            return .terminateNow
        case .waitVisible:
            waitInTheWindow()
            return .terminateCancel
        case .waitHidden(let limit):
            waitUnseen(sender, for: limit)
            return .terminateCancel
        case .alreadyWaiting:
            return .terminateCancel
        }
    }

    /// The window stays, or opens again if closing it was the quit, with the
    /// line and the progress on top; the app quits when the long work ends.
    /// Nothing is stopped: a restore has no safe point, and a delete or
    /// cleanup the person wants stopped has 멈추기.
    private func waitInTheWindow() {
        // Whatever is left after the long work gets its own unseen wait.
        quitBy = nil
        quitWhenDone = true
        sessions.setQuitsWhenDone(true)
        // Once AppKit has the reply and is done with this quit.
        Task { showWindow?() }
        guard windowWait == nil else { return }
        windowWait = Task {
            while longWork != nil {
                try? await Task.sleep(for: .milliseconds(200))
            }
            windowWait = nil
            if quitWhenDone { NSApp.terminate(nil) }
        }
    }

    /// The line's ✕: dotsync stays open after all.
    func keepOpen() {
        quitWhenDone = false
        sessions.setQuitsWhenDone(false)
    }

    private func waitUnseen(_ sender: NSApplication, for limit: Duration) {
        quitBy = ContinuousClock.now + limit
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
    }

    /// The Apple event behind this quit says logout, restart or shutdown. The
    /// reason is read as the attribute, as AppKit apps do, or the parameter,
    /// as AERegistry.h calls it.
    private var quitIsPowerOff: Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        let key = AEKeyword(kAEQuitReason)
        let reason = event.attributeDescriptor(forKeyword: key) ?? event.paramDescriptor(forKeyword: key)
        return QuitRules.isPowerOff(eventID: event.eventID, quitReason: reason?.typeCodeValue)
    }

    /// A logout or restart. AppKit posts the notification when the quit event
    /// arrives, before it asks `applicationShouldTerminate` (the reply also
    /// reads the quit's reason, in case it does not). The long runs get
    /// SIGTERM and stop at their next safe point, logging what they did; the
    /// cleanup unit relaunches nothing, so `dotsyncQuitChatGPT` stays set for
    /// the next launch's offer.
    private func powerOff() {
        guard !poweringOff else { return }
        let started = ContinuousClock.now
        powerOffStarted = started
        sessions.powerOff(at: started)
        let limit = QuitRules.powerOffStop
        powerOffDeadline = .now() + .milliseconds(Int(limit / .milliseconds(1)))
        let stop = powerOffStop
        stop.enter()
        // terminateAll blocks while it waits.
        DispatchQueue.global(qos: .userInitiated).async {
            _ = RunningCommands.shared.terminateAll(waitingUpTo: limit)
            stop.leave()
        }
    }
}
