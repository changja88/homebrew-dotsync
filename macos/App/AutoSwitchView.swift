import DotsyncKit
import ServiceManagement
import SwiftUI

/// The 자동 교체 menu (spec 5.7): the switch and one status line, the rule,
/// the fixed limits, the accounts it never picks, and the latest runs.
struct AutoSwitchView: View {
    let model: AutoSwitchModel
    let accounts: AccountsModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(spacing: 0) {
                ContentHeader(title: "자동 교체") { enabledSwitch }
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if let message = model.message {
                            MessageBar(text: message) { model.message = nil }
                        }
                        statusPanel(now: context.date)
                        // Two columns: the window is wide, and the lists are long.
                        HStack(alignment: .top, spacing: 12) {
                            rulePanel
                            limitsPanel.frame(width: 320)
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        HStack(alignment: .top, spacing: 12) {
                            exclusionPanel.frame(width: 320)
                            logPanel
                        }
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(EdgeInsets(top: 8, leading: 20, bottom: 18, trailing: 18))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .onAppear { model.reload() }
    }

    private var enabledSwitch: some View {
        HStack(spacing: 10) {
            Text(model.settings.enabled ? "켜짐" : "꺼짐")
                .font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Toggle("자동 교체", isOn: Binding(
                get: { model.settings.enabled },
                set: { on in Task { await model.setEnabled(on) } }
            ))
            .labelsHidden()
            .toggleStyle(.switch).controlSize(.small)
            .tint(Color(red: 47 / 255, green: 140 / 255, blue: 1))
        }
        .padding(.horizontal, 12).frame(height: 34)
        .background(GlassPanel(cornerRadius: 17))
        .fixedSize()
    }

    private func statusPanel(now: Date) -> some View {
        let live = UsageStore.shared()?.loadLive()
        let status = AutoSwitchText.status(settings: model.settings, agent: model.agent, file: accounts.file,
                                           live: live, now: now)
        let resting = model.settings.restUntil.map { $0 > now } ?? false
        let icon: (name: String, style: AnyShapeStyle) =
            if !model.settings.enabled { ("pause.circle", AnyShapeStyle(.secondary)) }
            else if model.agent != .on { ("exclamationmark.triangle", AnyShapeStyle(.orange)) }
            else if live == nil || accounts.file.unsavedSeat != nil { ("exclamationmark.circle", AnyShapeStyle(.orange)) }
            else if resting { ("hourglass", AnyShapeStyle(.secondary)) }
            else { ("eye", AnyShapeStyle(.tint)) }
        return HStack(spacing: 10) {
            Image(systemName: icon.name)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(icon.style)
            Text(status)
                .font(.system(size: 13, weight: .semibold))
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if model.agent == .needsApproval {
                Button("시스템 설정 열기") { SMAppService.openSystemSettingsLoginItems() }
                    .buttonStyle(PillButtonStyle())
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
        .background(GlassPanel(cornerRadius: 16))
    }

    private var rulePanel: some View {
        Panel(title: "다음 계정 고르기") {
            ForEach([AutoSwitchRule.even, .weeklyReset], id: \.self) { rule in
                Button { model.setRule(rule) } label: {
                    HStack(spacing: 10) {
                        Image(systemName: model.settings.rule == rule ? "largecircle.fill.circle" : "circle")
                            .font(.system(size: 15))
                            .foregroundStyle(model.settings.rule == rule ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                        VStack(alignment: .leading, spacing: 1) {
                            Text(AutoSwitchText.ruleTitle(rule)).font(.system(size: 13.5, weight: .semibold))
                            Text(AutoSwitchText.ruleDetail(rule)).font(.system(size: 11)).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var limitsPanel: some View {
        Panel(title: "바꾸는 때") {
            VStack(alignment: .leading, spacing: 4) {
                Text(AutoSwitchText.limits).font(.system(size: 15, weight: .bold))
                Text("쓰는 계정이 둘 중 하나에 닿으면 다음 계정으로 바꿔요")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var exclusionPanel: some View {
        Panel(title: "자동으로 고르지 않을 계정") {
            if accounts.file.accounts.isEmpty {
                Text("저장된 계정이 없어요").font(.system(size: 12)).foregroundStyle(.secondary)
            }
            ForEach(accounts.file.accounts) { account in
                let excluded = model.settings.excluded.contains(account.name)
                Button { model.setExcluded(account.name, !excluded) } label: {
                    HStack(spacing: 10) {
                        Image(systemName: excluded ? "checkmark.square.fill" : "square")
                            .font(.system(size: 15))
                            .foregroundStyle(excluded ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                        Text(account.label).font(.system(size: 13, weight: .semibold))
                        if account.label != account.name {
                            Text(account.name).font(.system(size: 11)).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var logPanel: some View {
        let labels = Dictionary(accounts.file.accounts.map { ($0.name, $0.label) },
                                uniquingKeysWith: { first, _ in first })
        return Panel(title: "최근 기록") {
            if model.log.isEmpty {
                Text("아직 기록이 없어요").font(.system(size: 12)).foregroundStyle(.secondary)
            }
            ForEach(Array(model.log.prefix(20).enumerated()), id: \.offset) { _, entry in
                HStack(spacing: 12) {
                    Text(UsageText.resetDate(entry.at))
                        .font(.system(size: 11.5)).monospacedDigit().foregroundStyle(.secondary)
                        .frame(width: 96, alignment: .leading)
                    Text(AutoSwitchText.logLine(entry, labels: labels))
                        .font(.system(size: 12.5, weight: entry.result == .switched ? .semibold : .regular))
                        .lineLimit(1).truncationMode(.tail)
                    Spacer(minLength: 0)
                }
            }
        }
    }
}

/// A titled glass panel of rows. It fills its row's height, so two panels
/// side by side end level.
private struct Panel<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            content
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(GlassPanel(cornerRadius: 16))
    }
}
