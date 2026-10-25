import AppKit
import DotsyncKit
import Observation

/// The 설정 menu's state. Its dotsync commands wait their turn in the queue it
/// shares with 사용량; the CLI's locks cover commands started elsewhere.
@MainActor
@Observable
final class SettingsModel {
    enum SyncSheet: Identifiable {
        /// nil: every tracked app (`--all`).
        case pull(app: String?)
        case push(app: String?)

        var id: String {
            switch self {
            case .pull(let app): "pull-\(app ?? "all")"
            case .push(let app): "push-\(app ?? "all")"
            }
        }
    }

    private(set) var report: StatusReport?
    /// From asking for a check to its answer, the wait in the queue included.
    private(set) var checking = false
    var message: String?
    var sheet: SyncSheet?
    let queue: CommandQueue
    let service: SyncService?

    init(queue: CommandQueue, service: SyncService? = SyncService.standard()) {
        self.queue = queue
        self.service = service
        if service == nil {
            message = "dotsync를 찾을 수 없어요 — brew install changja88/dotsync/dotsync"
        }
    }

    var isBusy: Bool { queue.running != nil || service == nil }

    /// The rail's count; none before the first check.
    var badge: Int { report.map(SettingsText.badgeCount) ?? 0 }

    /// Status takes seconds and may ask for the Automation permission, so it
    /// runs when 설정 is first shown and on 다시 확인, never on a timer.
    func checkFirstTime() async {
        guard report == nil, !checking else { return }
        await check()
    }

    func check() async {
        guard let service, !checking else { return }
        checking = true
        await queue.run("확인 중…") {
            do {
                self.report = try await service.status()
            } catch {
                self.message = Self.describe(error)
            }
        }
        checking = false
    }

    func setTracked(_ name: String, _ on: Bool) async {
        guard let service, let report else { return }
        let names = report.apps.filter { $0.name == name ? on : $0.tracked }.map(\.name)
        var saved = false
        await queue.run("저장 중…") {
            do {
                _ = try await service.setApps(names)
                saved = true
            } catch {
                self.message = Self.describe(error)
            }
        }
        guard saved else { return }
        // The switch stays where it was put while the check runs.
        for index in self.report?.apps.indices ?? 0..<0 {
            self.report?.apps[index].tracked = names.contains(report.apps[index].name)
        }
        await check()
    }

    /// Points dotsync at another folder; the files in it are not moved.
    func chooseFolder() async {
        guard let service else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "선택"
        panel.message = "dotsync가 설정을 맞춰 둘 폴더를 고르세요"
        if let dir = report?.dir { panel.directoryURL = URL(fileURLWithPath: dir) }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        var changed = false
        await queue.run("폴더 바꾸는 중…") {
            do {
                let result = try await service.setDir(url.path)
                changed = true
                if result.rc.action == "rc_missing" {
                    self.message = "터미널의 DOTSYNC_DIR은 직접 바꿔 주세요"
                }
            } catch {
                self.message = Self.describe(error)
            }
        }
        if changed { await check() }
    }

    /// System Settings › Privacy & Security › Automation, where dotsync gets
    /// to control BetterTouchTool.
    func openAutomationSettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation")!)
    }

    static func describe(_ error: Error) -> String {
        let failure = error as? CLIError ?? CLIError(code: "failed", message: error.localizedDescription)
        switch failure.code {
        case "busy": return "다른 dotsync 작업이 끝나면 다시 해 주세요"
        case "config": return "동기화 폴더를 찾지 못했어요 — 터미널에서 dotsync init을 먼저 해 주세요"
        case "cancelled": return "취소했어요"
        default: return failure.message
        }
    }
}
