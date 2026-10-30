import AppIntents
import DotsyncKit
import Foundation
import WidgetKit

// AudioPlaybackIntent makes the system run these in the app process,
// launching it without a window if needed (spike Q1, 2026-10-04). The widget
// extension is sandboxed and could not run dotsync itself.

/// ↻ on the widget.
struct RefreshIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "사용량 새로고침"

    // macOS redraws the widget only once perform() returns, so it marks the
    // file and returns; the app finishes the refresh while the widget shows
    // 조회 중….
    func perform() async throws -> some IntentResult {
        await IntentWork.startRefresh()
        return .result()
    }
}

/// "사용" on the widget.
struct UseAccountIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "계정 사용"

    @Parameter(title: "계정")
    var name: String

    init() {}

    init(name: String) {
        self.name = name
    }

    func perform() async throws -> some IntentResult {
        await IntentWork.begin()
        if let block = AutoSwitchStore.shared()?.manualSwitchBlock() {
            // A widget drawn before auto switch was turned on still has the
            // button: nothing switches, `finish` redraws the widget without
            // it, and a notification says why.
            await AutoSwitchNotices.post(AutoSwitchNotice(title: "자동 교체", body: block))
        } else if let service = await IntentWork.service() {
            switch await service.use(name) {
            case .switched:
                break
            case .needsConfirmation:
                // Claude's login changed since the last refresh; ask in the window.
                await IntentWork.open(AppLink.use(name).url)
            case .failed(let error):
                service.report(failure: error.message)
            }
        }
        await IntentWork.finish()
        return .result()
    }
}

/// ↻ on "지금 사용 중" while it shows Claude's own numbers for the account in
/// use: they are in live-usage.json already, so it only returns. A plain
/// AppIntent runs in the widget extension, and the system reloads the
/// widget's timeline once perform() returns, outside its daily budget
/// (WidgetKit, "Adding interactivity to widgets and Live Activities").
struct ShowLiveIntent: AppIntent {
    static let title: LocalizedStringResource = "실시간 사용량 보기"

    func perform() async throws -> some IntentResult {
        // The card shows as loading until the widget is drawn again, which
        // takes a blink: held for a moment so a press shows even when the
        // numbers have not moved.
        try await Task.sleep(for: .milliseconds(600))
        return .result()
    }
}

/// ↻ on "맥 상태": the widget reads the machine itself when its timeline is
/// reloaded, which the system does once perform() returns. Like ShowLiveIntent
/// it runs in the widget extension and wakes nothing else.
struct ReadSystemStatsIntent: AppIntent {
    static let title: LocalizedStringResource = "맥 상태 다시 읽기"

    func perform() async throws -> some IntentResult {
        .result()
    }
}

@MainActor
enum IntentWork {
    /// Intents still at work; the app keeps running until this is 0.
    private(set) static var running = 0

    static func begin() {
        running += 1
    }

    /// Marks usage.json before returning, then refreshes in the background.
    static func startRefresh() {
        begin()
        guard let service = service() else {
            finish()
            return
        }
        service.markRefreshing()
        Task {
            _ = await RefreshGate.shared.refresh(service)
            finish()
        }
    }

    /// The service, or nil after noting in usage.json why there is none.
    static func service() -> AccountService? {
        if let service = AccountService.standard(didSave: { WidgetCenter.shared.reloadAllTimelines() }) {
            return service
        }
        if let store = UsageStore.shared() {
            try? store.save(UsageMerge.failed(previous: store.load(), message: "dotsync를 찾을 수 없어요"))
        }
        return nil
    }

    static func open(_ url: URL) {
        AppHooks.open?(url)
    }

    static func finish() {
        running -= 1
        NotificationCenter.default.post(name: .usageFileChanged, object: nil)
        WidgetCenter.shared.reloadAllTimelines()
        AppHooks.afterIntent?()
    }
}
