import DotsyncKit
import Foundation
import Observation
import UserNotifications
import WidgetKit

/// The 자동 교체 menu's state, and the run the checker wakes the app for.
/// A run takes the 사용량 queue, so it never overlaps a command the window
/// started; the CLI's lock covers commands from elsewhere.
@MainActor
@Observable
final class AutoSwitchModel {
    private(set) var settings: AutoSwitchSettings
    private(set) var log: [AutoSwitchLogEntry]
    private(set) var agent: AgentState
    private(set) var isRunning = false
    /// What the switch is turning to, while that takes (the notification
    /// prompt, launchctl): the switch shows it and can't be pressed again.
    private(set) var turning: Bool?
    /// Why turning auto switch on failed.
    var message: String?
    private let queue: CommandQueue
    private let store: AutoSwitchStore?
    /// What the menu shows follows the files, whoever changed them: a list
    /// left stale there turns a click into the wrong account's flip.
    @ObservationIgnored private var watch: FolderWatch?

    init(queue: CommandQueue, store: AutoSwitchStore? = .shared()) {
        self.queue = queue
        self.store = store
        let settings = store?.loadSettings() ?? .off
        self.settings = settings
        log = store?.loadLog() ?? []
        agent = AgentRegistration.state(enabled: settings.enabled)
        watch = store?.watch { [weak self] in
            MainActor.assumeIsolated { self?.reload() }
        }
    }

    func reload() {
        settings = store?.loadSettings() ?? .off
        log = store?.loadLog() ?? []
        agent = AgentRegistration.state(enabled: settings.enabled)
    }

    /// Turning it on asks for notifications the first time (refused, auto
    /// switch still works) and puts the agent into launchd.
    func setEnabled(_ on: Bool) async {
        guard let store, turning == nil else { return }
        turning = on
        defer { turning = nil }
        message = nil
        if on {
            guard let wanted = AgentRegistration.wanted(),
                  AutoSwitchAgent.action(enabled: true, installed: nil, wanted: wanted) == .install else {
                message = "dotsync를 응용 프로그램 폴더에서 연 뒤 켜 주세요"
                return
            }
            if !settings.enabled {
                _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
            }
            try? store.updateSettings { $0.enabled = true }
            do {
                try await Task.detached { try AgentRegistration.install(wanted) }.value
            } catch {
                try? store.updateSettings { $0.enabled = false }
                let reason = (error as? AutoSwitchAgent.InstallFailure)?.message ?? error.localizedDescription
                message = "자동 교체를 켜지 못했어요 — \(reason)"
            }
        } else {
            try? store.updateSettings { $0.enabled = false }
            await Task.detached { AgentRegistration.remove() }.value
        }
        reload()
        // The widget's 사용 buttons come and go with it.
        WidgetCenter.shared.reloadAllTimelines()
    }

    func setRule(_ rule: AutoSwitchRule) {
        try? store?.updateSettings { $0.rule = rule }
        reload()
    }

    func setExcluded(_ name: String, _ excluded: Bool) {
        try? store?.updateSettings { settings in
            settings.excluded.removeAll { $0 == name }
            if excluded { settings.excluded.append(name) }
        }
        reload()
    }

    /// The checker's wake-up. Another arriving while one runs is dropped:
    /// the run reads the newest numbers anyway.
    func handle(_ request: AutoSwitchRequest) async {
        guard !isRunning, let store else { return }
        isRunning = true
        let previous = store.loadLog().first
        var entry: AutoSwitchLogEntry?
        if let accounts = AccountService.standard(didSave: { WidgetCenter.shared.reloadAllTimelines() }) {
            await queue.run("자동 교체 중") {
                entry = await AutoSwitchService(accounts: accounts, store: store).run(request)
            }
        } else if store.loadSettings().enabled {
            // Resting keeps every new live number from waking the app again.
            entry = AutoSwitchService.recordUnavailable(store, message: "dotsync를 찾을 수 없어요")
        }
        if let entry, let notice = AutoSwitchText.notification(for: entry, previous: previous, labels: labels()) {
            await AutoSwitchNotices.post(notice)
        }
        reload()
        NotificationCenter.default.post(name: .usageFileChanged, object: nil)
        WidgetCenter.shared.reloadAllTimelines()
        isRunning = false
        AppHooks.afterIntent?()
    }

    /// At launch: the agent follows the setting and this copy of the app.
    func reconcileAgent() {
        let enabled = settings.enabled
        Task {
            await Task.detached { AgentRegistration.reconcile(enabled: enabled) }.value
            agent = AgentRegistration.state(enabled: enabled)
        }
    }

    private func labels() -> [String: String] {
        Dictionary((UsageStore.shared()?.load()?.accounts ?? []).map { ($0.name, $0.label) },
                   uniquingKeysWith: { first, _ in first })
    }
}
