import DotsyncKit
import SwiftUI

/// The 설정 menu, as the approved mockup `E-settings`: the folder and when it
/// was checked on top, a banner when apps differ, then one row per app.
struct SettingsView: View {
    @Bindable var model: SettingsModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(spacing: 0) {
                ContentHeader(title: "동기화 설정") { headerPills(now: context.date) }
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if let message = model.message {
                            MessageBar(text: message) { model.message = nil }
                        }
                        if let report = model.report {
                            if let banner = SettingsText.banner(report) {
                                DifferBanner(title: banner, note: SettingsText.directionNote(report), model: model)
                            }
                            SettingsTable(report: report, model: model)
                        }
                        Footnotes()
                    }
                    .padding(EdgeInsets(top: 8, leading: 20, bottom: 18, trailing: 18))
                }
            }
        }
        // Not .task: leaving 설정 must not stop a check that has started.
        .onAppear { Task { await model.checkFirstTime() } }
        .sheet(item: $model.sheet) { sheet in
            SyncSheetView(sheet: sheet, model: model)
        }
    }

    private func headerPills(now: Date) -> some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Text(checkedText(now: now)).font(.system(size: 12)).foregroundStyle(.secondary)
                Button { Task { await model.check() } } label: {
                    Label("다시 확인", systemImage: "arrow.clockwise")
                }
                .buttonStyle(PillButtonStyle())
                .disabled(model.isBusy)
            }
            .padding(.leading, 12).padding(3)
            .background(GlassPanel(cornerRadius: 17))
            HStack(spacing: 8) {
                Image(systemName: "folder").font(.system(size: 13)).foregroundStyle(.secondary)
                Text(model.report.map { ($0.dir as NSString).abbreviatingWithTildeInPath } ?? "—")
                    .font(.system(size: 12.5, weight: .semibold))
                    .lineLimit(1).truncationMode(.middle)
                    .frame(maxWidth: 260)
                Button("다른 폴더…") { Task { await model.chooseFolder() } }
                    .buttonStyle(PillButtonStyle())
                    .disabled(model.isBusy)
            }
            .padding(.leading, 12).padding(3)
            .background(GlassPanel(cornerRadius: 17))
        }
        .fixedSize()
    }

    private func checkedText(now: Date) -> String {
        if model.checking { return "확인 중…" }
        guard let report = model.report else { return "확인 전" }
        return SettingsText.checkedAgo(report.checkedAt, now: now)
    }
}

/// "N개 앱이 폴더와 달라요" on an orange-tinted glass, with "모두 …" buttons.
struct DifferBanner: View {
    let title: String
    let note: String?
    let model: SettingsModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(ChipStyle.different.foreground(dark: colorScheme == .dark))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 13.5, weight: .bold))
                if let note {
                    Text(note).font(.system(size: 11.5)).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
            Button { model.sheet = .pull(app: nil) } label: {
                Label("모두 가져오기…", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(PillButtonStyle(height: 30))
            Button { model.sheet = .push(app: nil) } label: {
                Label("모두 내보내기…", systemImage: "square.and.arrow.up")
            }
            .buttonStyle(PillButtonStyle(height: 30))
        }
        .disabled(model.isBusy)
        .padding(EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 14))
        .background(GlassPanel(cornerRadius: 16, tint: .orange, tintStrength: 0.12))
    }
}

/// The mockup's columns: 앱, 상태, 바뀐 내용, 받기 · 보내기, 동기화.
enum SettingsColumns {
    static let app: CGFloat = 190
    static let state: CGFloat = 110
    static let actions: CGFloat = 68
    static let toggle: CGFloat = 40
    static let spacing: CGFloat = 16
}

struct SettingsTable: View {
    let report: StatusReport
    let model: SettingsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: SettingsColumns.spacing) {
                Text("앱 \(report.apps.count)").frame(width: SettingsColumns.app, alignment: .leading)
                Text("상태").frame(width: SettingsColumns.state, alignment: .leading)
                Text("바뀐 내용").frame(maxWidth: .infinity, alignment: .leading)
                Text("받기 · 보내기").fixedSize().frame(width: SettingsColumns.actions, alignment: .leading)
                Text("동기화").frame(width: SettingsColumns.toggle, alignment: .leading)
            }
            .font(.system(size: 11.5, weight: .semibold)).foregroundStyle(.secondary)
            .padding(.horizontal, 18)
            VStack(spacing: 0) {
                ForEach(Array(report.apps.enumerated()), id: \.element.id) { index, app in
                    if index > 0 {
                        Rectangle().fill(.primary.opacity(0.08)).frame(height: 1).padding(.horizontal, 16)
                    }
                    SettingsRow(app: app, model: model)
                }
            }
            .padding(.vertical, 3)
            .background(GlassPanel(cornerRadius: 16))
        }
    }
}

struct SettingsRow: View {
    let app: AppSyncStatus
    let model: SettingsModel

    var body: some View {
        let label = SettingsText.appLabel(name: app.name)
        // A clean or untracked app has nothing to bring over.
        let canSync = app.tracked && app.state != "clean" && !model.isBusy
        HStack(spacing: SettingsColumns.spacing) {
            VStack(alignment: .leading, spacing: 1) {
                Text(label).font(.system(size: 13.5, weight: .semibold)).lineLimit(1)
                if let sub = subLine {
                    HStack(spacing: 6) {
                        Text(sub).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                        if app.reason == "btt_permission" {
                            Button("권한 열기") { model.openAutomationSettings() }
                                .buttonStyle(.link).font(.system(size: 11, weight: .semibold))
                        }
                    }
                }
            }
            .frame(width: SettingsColumns.app, alignment: .leading)
            StateChip(app: app).frame(width: SettingsColumns.state, alignment: .leading)
            changesCell.frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 6) {
                Button { model.sheet = .pull(app: app.name) } label: {
                    Image(systemName: "square.and.arrow.down")
                }
                .help("\(label) 가져오기")
                Button { model.sheet = .push(app: app.name) } label: {
                    Image(systemName: "square.and.arrow.up")
                }
                .help("\(label) 내보내기")
            }
            .buttonStyle(IconPillButtonStyle())
            .disabled(!canSync)
            .frame(width: SettingsColumns.actions, alignment: .leading)
            Toggle("\(label) 동기화", isOn: Binding(
                get: { app.tracked },
                set: { on in Task { await model.setTracked(app.name, on) } }
            ))
            .labelsHidden()
            .toggleStyle(.switch).controlSize(.small)
            .tint(Color(red: 47 / 255, green: 140 / 255, blue: 1))
            .disabled(model.isBusy || model.report == nil)
            .frame(width: SettingsColumns.toggle, alignment: .leading)
        }
        .padding(.horizontal, 18).padding(.vertical, 8)
    }

    private var subLine: String? {
        guard app.tracked else { return nil }
        if app.name == "bettertouchtool",
           let line = SettingsText.bttLine(presets: app.options?.presets ?? [], reason: app.reason) {
            return line
        }
        return app.state == "unknown" ? app.details : nil
    }

    @ViewBuilder private var changesCell: some View {
        if app.tracked, let files = SettingsText.changedFiles(app.changes ?? []) {
            VStack(alignment: .leading, spacing: 1) {
                Text(files).font(.system(size: 11, design: .monospaced)).lineLimit(1).truncationMode(.middle)
                if let keys = SettingsText.changedKeys(app.changes ?? []) {
                    Text(keys).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        } else if app.tracked, app.state == "missing", let details = app.details, !details.isEmpty {
            Text(details).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1)
        } else {
            Text("—").font(.system(size: 12)).foregroundStyle(.secondary)
        }
    }
}

/// The 상태 column's chip, in the mockup's four colors.
enum ChipStyle {
    case same, different, newerHere, unknown

    init?(_ app: AppSyncStatus) {
        guard app.tracked else { return nil }
        switch app.state {
        case "clean": self = .same
        case "dirty" where app.direction == "local-newer": self = .newerHere
        case "dirty", "missing": self = .different
        case "unknown": self = .unknown
        default: return nil
        }
    }

    func foreground(dark: Bool) -> Color {
        switch self {
        case .same: dark ? Color(red: 111 / 255, green: 227 / 255, blue: 148 / 255) : Color(red: 26 / 255, green: 102 / 255, blue: 49 / 255)
        case .different: dark ? Color(red: 1, green: 177 / 255, blue: 92 / 255) : Color(red: 163 / 255, green: 71 / 255, blue: 0)
        case .newerHere: dark ? Color(red: 138 / 255, green: 196 / 255, blue: 1) : Color(red: 10 / 255, green: 88 / 255, blue: 194 / 255)
        case .unknown: .secondary
        }
    }

    func background(dark: Bool) -> Color {
        switch self {
        case .same: dark ? Color(red: 48 / 255, green: 209 / 255, blue: 88 / 255).opacity(0.2) : Color(red: 47 / 255, green: 180 / 255, blue: 87 / 255).opacity(0.16)
        case .different: dark ? Color(red: 1, green: 159 / 255, blue: 10 / 255).opacity(0.22) : Color(red: 1, green: 149 / 255, blue: 0).opacity(0.18)
        case .newerHere: dark ? Color(red: 10 / 255, green: 132 / 255, blue: 1).opacity(0.26) : Color(red: 10 / 255, green: 122 / 255, blue: 1).opacity(0.13)
        case .unknown: dark ? Color(red: 235 / 255, green: 235 / 255, blue: 245 / 255).opacity(0.16) : Color(red: 60 / 255, green: 60 / 255, blue: 67 / 255).opacity(0.14)
        }
    }
}

struct StateChip: View {
    let app: AppSyncStatus
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let label = SettingsText.stateLabel(app)
        if let style = ChipStyle(app) {
            let dark = colorScheme == .dark
            Text(label)
                .font(.system(size: 11, weight: .bold)).lineLimit(1)
                .foregroundStyle(style.foreground(dark: dark))
                .padding(.horizontal, 8).frame(height: 20)
                .background(Capsule().fill(style.background(dark: dark)))
        } else {
            Text(label).font(.system(size: 12)).foregroundStyle(.secondary)
        }
    }
}

/// The 받기 · 보내기 buttons: a 30 × 28 white glass pill around a symbol.
struct IconPillButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.primary)
            .frame(width: 30, height: 28)
            .background(GlassPanel(cornerRadius: 14, milk: 0.7, shadowRadius: 0))
            .contentShape(Capsule())
            .opacity(configuration.isPressed ? 0.7 : isEnabled ? 1 : 0.4)
    }
}

/// The mockup's two notes under the table.
struct Footnotes: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            note("폴더를 바꾸면 터미널이 쓰는 DOTSYNC_DIR(~/.zshrc)도 같이 바꿔요. 폴더 안의 파일은 옮기지 않아요.")
            note("가져오기·내보내기는 바뀌는 내용을 먼저 보여 준 뒤에 해요. BetterTouchTool을 처음 비교할 때 macOS가 자동화 권한을 한 번 물어봐요.")
        }
        .font(.system(size: 11.5)).foregroundStyle(.secondary)
        .padding(.horizontal, 4).padding(.top, 2)
    }

    private func note(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            Image(systemName: "info.circle").font(.system(size: 11.5))
            Text(text).lineSpacing(2).fixedSize(horizontal: false, vertical: true)
        }
    }
}
