import DotsyncKit
import Foundation
import Observation
import WidgetKit

/// The window's state. dotsync runs one command at a time from here; the
/// CLI's lock covers commands started elsewhere.
@MainActor
@Observable
final class AccountsModel {
    enum Sheet: Identifiable {
        case add(thenUse: String?)
        case relogin(AccountUsage)
        case rename(AccountUsage)
        case confirmSwitch(name: String, email: String?)

        var id: String {
            switch self {
            case .add: "add"
            case .relogin(let account): "relogin-\(account.name)"
            case .rename(let account): "rename-\(account.name)"
            case .confirmSwitch(let name, _): "switch-\(name)"
            }
        }
    }

    private(set) var file: UsageFile
    private(set) var running: String?
    var message: String?
    var sheet: Sheet?
    var pendingRemoval: AccountUsage?
    let service: AccountService?

    init(service: AccountService? = AccountService.standard(didSave: { WidgetCenter.shared.reloadAllTimelines() })) {
        self.service = service
        file = service?.current() ?? .empty
        if service == nil {
            message = DotsyncCLI.locate() == nil
                ? "dotsync를 찾을 수 없어요 — brew install changja88/dotsync/dotsync"
                : "위젯과 함께 쓰는 폴더를 열 수 없어요"
        }
        NotificationCenter.default.addObserver(forName: .usageFileChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.reload() }
        }
    }

    var isBusy: Bool { running != nil || service == nil }

    var accounts: [AccountUsage] { UsageOrder.sorted(file.accounts) }

    func reload() {
        if let service { file = service.current() }
    }

    func refresh() async {
        await perform("조회 중") { service in
            self.file = await RefreshGate.shared.refresh(service)
            self.message = self.file.lastError
        }
    }

    func use(_ name: String, overwriteUnsaved: Bool = false) async {
        await perform("교체 중") { service in
            switch await service.use(name, overwriteUnsaved: overwriteUnsaved) {
            case .switched(let file):
                self.file = file
                self.message = file.lastError
            case .needsConfirmation(let email):
                self.sheet = .confirmSwitch(name: name, email: email)
            case .failed(let error):
                self.message = Self.describe(error)
            }
        }
    }

    /// Waits for the browser; cancel by cancelling the calling task.
    func login(_ name: String) async -> Result<AccountInfo, CLIError> {
        guard let service, running == nil else {
            return .failure(CLIError(code: "busy", message: "busy"))
        }
        running = "로그인 기다리는 중"
        let outcome = await service.login(name)
        running = nil
        if case .success = outcome {
            Task { await refresh() }
        }
        return outcome
    }

    /// nil when renamed, otherwise why not.
    func rename(_ name: String, to label: String) async -> String? {
        var failure: String?
        await perform("이름 바꾸는 중") { service in
            switch await service.rename(name, to: label) {
            case .success(let file): self.file = file
            case .failure(let error): failure = Self.describe(error)
            }
        }
        return failure
    }

    func remove(_ name: String) async {
        var removed = false
        await perform("삭제 중") { service in
            switch await service.remove(name) {
            case .success(let file):
                self.file = file
                removed = true
            case .failure(let error):
                self.message = Self.describe(error)
            }
        }
        if removed { await refresh() }
    }

    func handle(_ url: URL) {
        switch AppLink(url: url) {
        case .use(let name)?:
            sheet = .confirmSwitch(name: name, email: file.unsavedSeat?.email)
        case .relogin(let name)?:
            if let account = file.accounts.first(where: { $0.name == name }) {
                sheet = .relogin(account)
            }
        case .open?, nil:
            break
        }
    }

    private func perform(_ label: String, _ work: (AccountService) async -> Void) async {
        guard let service, running == nil else { return }
        running = label
        await work(service)
        running = nil
        WidgetCenter.shared.reloadAllTimelines()
    }

    static func describe(_ error: CLIError) -> String {
        switch error.code {
        case "busy": "다른 작업 중이에요 — 잠시 후 다시 해 주세요"
        case "claude_missing": "Claude Code가 설치돼 있지 않아요"
        case "login_required": "로그인이 풀렸어요 — 다시 로그인해 주세요"
        case "wrong_account": "브라우저에서 승인한 계정이 달라요 — 크롬을 그 계정으로 바꾸고 다시 해 주세요"
        case "cancelled": "취소했어요"
        case "not_found": "이 계정이 이 맥에 없어요 — 새로고침해 주세요"
        default: error.message
        }
    }
}
