import DotsyncKit
import SwiftUI

/// "… 설정을 이 맥으로 가져올까요?", as the approved mockup `E-pull`: what
/// changes, shown before anything does, then 취소 or 가져오기 (내보내기).
struct SyncSheetView: View {
    let sheet: SettingsModel.SyncSheet
    let model: SettingsModel
    @State private var preview: SyncPreview?
    @State private var failure: String?
    @State private var showsDiff = false
    @State private var applying = false

    private var isPull: Bool { sheet.direction == .pull }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 17, weight: .bold))
                Text(subtitle).font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
            ChangeBox {
                content
            }
            if showsDiff, let preview {
                ScrollView {
                    Text(Self.diffText(preview))
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                .frame(height: 220)
                .background(RoundedRectangle(cornerRadius: 12).fill(.primary.opacity(0.05)))
            }
            if isPull, changedPlans.contains(where: { $0.app == "claude" }) {
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Image(systemName: "info.circle")
                    Text("플러그인은 dotsync가 claude plugin 명령으로 설치하고 꺼요. 터미널에서 dotsync pull claude를 한 것과 같아요.")
                        .lineSpacing(2).fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: 11.5)).foregroundStyle(.secondary)
                .padding(.horizontal, 2)
            }
            HStack(spacing: 8) {
                Spacer()
                Button("취소") { model.sheet = nil }
                    .buttonStyle(PillButtonStyle(height: 32))
                    .keyboardShortcut(.cancelAction)
                    .disabled(applying)
                Button(applying ? (isPull ? "가져오는 중…" : "내보내는 중…") : (isPull ? "가져오기" : "내보내기")) {
                    applying = true
                    Task { await model.apply(sheet) }
                }
                .buttonStyle(PillButtonStyle(prominent: true, height: 32))
                .keyboardShortcut(.defaultAction)
                .disabled(applying || changedPlans.isEmpty)
            }
            .padding(.top, 4)
        }
        .padding(EdgeInsets(top: 20, leading: 22, bottom: 18, trailing: 22))
        .frame(width: 580)
        .interactiveDismissDisabled(applying)
        .task { await load() }
    }

    private var title: String {
        let label = sheet.app.map { SettingsText.appLabel(name: $0) } ?? "모든 앱"
        return isPull ? SettingsText.pullTitle(appLabel: label) : SettingsText.pushTitle(appLabel: label)
    }

    private var subtitle: String {
        let path = preview?.dir ?? model.report?.dir ?? "동기화 폴더"
        let dir = (path as NSString).abbreviatingWithTildeInPath
        return isPull ? "\(dir)에 있는 설정으로 이 맥의 설정을 바꿔요." : "이 맥의 설정으로 \(dir) 폴더를 바꿔요."
    }

    /// The plans that change something; "모두 …" previews every tracked app.
    private var changedPlans: [SyncPlan] {
        (preview?.plans ?? []).filter { plan in plan.changes.contains { $0.kind != "unchanged" } }
    }

    @ViewBuilder private var content: some View {
        HStack(spacing: 8) {
            Text(isPull ? "이 맥에서 바뀌는 것" : "폴더에서 바뀌는 것").font(.system(size: 12.5, weight: .bold))
            Spacer()
            if let preview, preview.plans.contains(where: { $0.changes.contains { $0.diff != nil } }) {
                Button(showsDiff ? "차이 숨기기" : "전체 차이 보기") { showsDiff.toggle() }
                    .buttonStyle(PillButtonStyle(height: 26))
            }
        }
        if let failure {
            Text(failure).font(.system(size: 12)).foregroundStyle(.secondary)
        } else if preview == nil {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("바뀌는 내용을 확인하는 중…").font(.system(size: 12)).foregroundStyle(.secondary)
            }
        } else if changedPlans.isEmpty {
            Text("바뀌는 것이 없어요").font(.system(size: 12)).foregroundStyle(.secondary)
        } else {
            ViewThatFits(in: .vertical) {
                planRows
                ScrollView { planRows }
            }
            .frame(maxHeight: 300)
        }
    }

    private var planRows: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(changedPlans) { plan in
                if changedPlans.count > 1 {
                    Text(SettingsText.appLabel(name: plan.app))
                        .font(.system(size: 12, weight: .bold)).padding(.top, 2)
                }
                ForEach(Array(plan.changes.filter { $0.kind != "unchanged" }.enumerated()), id: \.offset) { _, change in
                    ChangeLine(name: change.label, value: change.details.isEmpty ? Self.kindText(change.kind) : change.details,
                               monospaced: true)
                }
                if let actions = plan.pluginActions, let text = SettingsText.pluginActionsText(actions) {
                    ChangeLine(name: "플러그인", value: text)
                }
                if plan.app == "claude" {
                    ChangeLine(name: "바꾸지 않는 것", value: "로그인 · 대화 기록 · 프로젝트 기억(memory)", secondary: true)
                }
            }
        }
    }

    private func load() async {
        switch await model.preview(sheet) {
        case .success(let result): preview = result
        case .failure(let error): failure = SettingsModel.describe(error)
        }
    }

    private static func kindText(_ kind: String) -> String {
        switch kind {
        case "create": "새로 생겨요"
        case "remove": "지워져요"
        case "missing-source": "폴더에 없어요"
        case "unknown": "확인 못 함"
        default: "바뀌어요"
        }
    }

    private static func diffText(_ preview: SyncPreview) -> String {
        preview.plans.flatMap { plan in
            plan.changes.compactMap { change in
                change.diff.map { "── \(SettingsText.appLabel(name: plan.app)) · \(change.label)\n\($0)" }
            }
        }
        .joined(separator: "\n\n")
    }
}

/// The mockup's box: glass with a thin line around it.
struct ChangeBox<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) { content }
            .padding(.horizontal, 14).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(GlassPanel(cornerRadius: 12, shadowRadius: 0))
    }
}

/// One line in the box: a 150 pt name column, then what happens.
struct ChangeLine: View {
    let name: String
    let value: String
    var monospaced = false
    var secondary = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(name)
                .font(monospaced ? .system(size: 11.5, design: .monospaced) : .system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle)
                .frame(width: 150, alignment: .leading)
            Text(value).font(.system(size: 12))
                .foregroundStyle(secondary ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
