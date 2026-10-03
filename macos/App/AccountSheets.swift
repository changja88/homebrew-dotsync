import DotsyncKit
import SwiftUI

struct AccountSheet: View {
    let sheet: AccountsModel.Sheet
    let model: AccountsModel

    var body: some View {
        switch sheet {
        case .add(let thenUse):
            LoginSheet(model: model, fixedName: nil, thenUse: thenUse)
        case .relogin(let account):
            LoginSheet(model: model, fixedName: account.name, thenUse: nil)
        case .rename(let account):
            RenameSheet(model: model, account: account)
        case .confirmSwitch(let name, let email):
            ConfirmSwitchSheet(model: model, name: name, email: email)
        }
    }
}

/// "계정 추가" and "다시 로그인": runs `dotsync account login` and waits for
/// the browser. 취소 while waiting cancels the login (SIGTERM).
struct LoginSheet: View {
    let model: AccountsModel
    let fixedName: String?
    let thenUse: String?
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var task: Task<Void, Never>?
    @State private var result: String?
    @State private var failed = false

    private var target: String { fixedName ?? name }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(fixedName == nil ? "계정 추가" : "다시 로그인 · \(fixedName!)").font(.headline)
            if fixedName == nil {
                Text("이름 (위젯과 이 창에 보이는 이름)").font(.caption).foregroundStyle(.secondary)
                TextField("예: work", text: $name).disabled(task != nil)
                if !name.isEmpty && !AccountRules.isValidName(name) {
                    Text("영문·숫자로 시작하고 영문·숫자·. _ - 만 쓸 수 있어요").font(.caption).foregroundStyle(.red)
                }
            }
            Text("""
            1. 크롬에서 claude.ai가 \(fixedName == nil ? "추가할" : "이") 계정으로 로그인돼 있는지 확인
            2. "브라우저에서 로그인"을 누르고, 열린 크롬 화면에서 승인
            3. 끝나면 저장된 이메일이 여기에 표시돼요
            """)
            .font(.callout)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 8))
            if task != nil {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("브라우저에서 승인 기다리는 중…")
                }
            }
            if let result {
                Text(result).foregroundStyle(failed ? .red : .primary)
            }
            HStack {
                Spacer()
                Button("취소") {
                    if let task { task.cancel() } else { dismiss() }
                }
                Button("브라우저에서 로그인") { start() }
                    .buttonStyle(.borderedProminent)
                    .disabled(task != nil || !AccountRules.isValidName(target))
            }
        }
        .padding(20)
        .frame(width: 380)
        .interactiveDismissDisabled(task != nil)
    }

    private func start() {
        result = nil
        task = Task {
            let outcome = await model.login(target)
            task = nil
            switch outcome {
            case .success(let info):
                failed = false
                result = "저장됨: \(info.email ?? info.name)"
                try? await Task.sleep(for: .seconds(1.2))
                dismiss()
                if let thenUse {
                    await model.use(thenUse)
                }
            case .failure(let error):
                failed = true
                result = AccountsModel.describe(error)
            }
        }
    }
}

struct RenameSheet: View {
    let model: AccountsModel
    let account: AccountUsage
    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("이름 바꾸기").font(.headline)
            TextField("보이는 이름", text: $label)
            Text("1~\(AccountRules.labelMax)자 · 명령에 쓰는 이름(\(account.name))과 로그인은 그대로예요")
                .font(.caption).foregroundStyle(.secondary)
            if let error {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("취소") { dismiss() }
                Button("저장") {
                    Task {
                        error = await model.rename(account.name, to: label)
                        if error == nil { dismiss() }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!AccountRules.isValidLabel(label) || model.isBusy)
            }
        }
        .padding(20)
        .frame(width: 340)
        .onAppear { label = account.label }
    }
}

/// Mockup ③: switching would drop a login no saved account holds.
struct ConfirmSwitchSheet: View {
    let model: AccountsModel
    let name: String
    let email: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("지금 로그인을 저장할까요?").font(.headline)
            Text("Claude가 지금 쓰는 로그인(\(email ?? "알 수 없는 계정"))이 앱에 저장돼 있지 않아요. 저장하지 않고 바꾸면 이 로그인은 사라져요.")
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("취소") { dismiss() }
                Button("저장 안 하고 바꾸기") {
                    dismiss()
                    Task { await model.use(name, overwriteUnsaved: true) }
                }
                Button("먼저 저장하기…") { model.sheet = .add(thenUse: name) }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
