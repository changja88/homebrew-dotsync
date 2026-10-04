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

    func perform() async throws -> some IntentResult {
        await IntentWork.begin()
        if let service = await IntentWork.service() {
            _ = await RefreshGate.shared.refresh(service)
        }
        await IntentWork.finish()
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
        if let service = await IntentWork.service() {
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

@MainActor
enum IntentWork {
    /// Intents still at work; the app keeps running until this is 0.
    private(set) static var running = 0

    static func begin() {
        running += 1
    }

    /// The service, or nil after noting in usage.json why there is none.
    static func service() -> AccountService? {
        if let service = AccountService.standard() { return service }
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
