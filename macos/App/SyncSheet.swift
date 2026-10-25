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
                    .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
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
        // The system sheet is clear glass: the table showed through the title.
        .presentationBackground { SheetBackdrop() }
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
                    .buttonStyle(PillButtonStyle(height: 26, shadowRadius: 1.5))
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
        } else if changedPlans.reduce(0, { $0 + $1.changes.count + 2 }) > 12 {
            // A long "모두 …" list scrolls; a short one keeps the sheet small.
            ScrollView { planRows }.frame(height: 300)
        } else {
            planRows
        }
    }

    private var planRows: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(changedPlans) { plan in
                if changedPlans.count > 1 {
                    Text(SettingsText.appLabel(name: plan.app))
                        .font(.system(size: 12, weight: .bold)).padding(.top, 2)
                }
                let changes = plan.changes.filter { $0.kind != "unchanged" }
                let plugins = plan.pluginActions.flatMap(SettingsText.pluginActionsText)
                // The plugin actions read as what installed_plugins.json's change does.
                let carrier = plugins == nil ? nil : changes.firstIndex { $0.label.hasSuffix("installed_plugins.json") }
                ForEach(changes.indices, id: \.self) { index in
                    let change = changes[index]
                    ChangeLine(name: Self.fileName(change.label),
                               value: index == carrier ? plugins!
                                   : change.details.isEmpty ? Self.kindText(change.kind) : change.details,
                               monospaced: true)
                }
                if carrier == nil, let plugins {
                    ChangeLine(name: "플러그인", value: plugins)
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

    /// "plugins/installed_plugins.json" → "installed_plugins.json"; a folder
    /// ("rules/") keeps its name.
    private static func fileName(_ label: String) -> String {
        label.hasSuffix("/") ? label : (label as NSString).lastPathComponent
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

/// The mockup's sheet ground (--sheet): frosted, nearly opaque.
struct SheetBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            Rectangle().fill(.regularMaterial)
            colorScheme == .dark
                ? Color(red: 44 / 255, green: 46 / 255, blue: 56 / 255).opacity(0.92)
                : Color(red: 250 / 255, green: 250 / 255, blue: 253 / 255).opacity(0.86)
        }
    }
}

/// The mockup's box: glass with a thin line around it, which a white
/// glass panel alone would lose on the white sheet.
struct ChangeBox<Content: View>: View {
    @ViewBuilder var content: Content
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 12, style: .continuous)
        VStack(alignment: .leading, spacing: 6) { content }
            .padding(.horizontal, 14).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                shape.fill(colorScheme == .dark
                           ? Color(red: 60 / 255, green: 62 / 255, blue: 76 / 255).opacity(0.38)
                           : .white.opacity(0.42))
                    .overlay(shape.strokeBorder(.primary.opacity(0.1), lineWidth: 1))
            }
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
                .font(.system(size: monospaced ? 11.5 : 12, weight: .semibold, design: monospaced ? .monospaced : .default))
                .foregroundStyle(.secondary)
                .lineLimit(1).fixedSize()
                .frame(minWidth: 150, alignment: .leading)
            Text(value).font(.system(size: 12))
                .foregroundStyle(secondary ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
