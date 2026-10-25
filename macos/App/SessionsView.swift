import DotsyncKit
import SwiftUI

/// The 세션 menu, as the approved mockups `F-claude` and `F-codex`: the tool
/// tabs and totals on top, quick-select buttons, the sessions grouped by
/// project, and the bar with what is picked.
struct SessionsView: View {
    let model: SessionsModel
    /// Remembered across launches.
    @AppStorage("sessionsTab") private var tool: SessionTool = .claude

    var body: some View {
        let state = model[tool]
        VStack(spacing: 0) {
            ContentHeader(title: "세션") { header(state) }
            if let message = state.message {
                MessageBar(text: message) { model.dismissMessage(tool) }
                    .padding(.horizontal, 20).padding(.top, 8)
            }
            if let list = state.list {
                // A new pane per tool: its scroll position and rows start clean.
                SessionsPane(model: model, tool: tool, state: state, list: list).id(tool)
            } else {
                Spacer(minLength: 0)
                Text(state.checking ? "세션을 읽는 중…" : "아직 읽지 않았어요")
                    .font(.system(size: 12.5)).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Not .task: leaving 세션 must not stop a list that has started.
        .onAppear { Task { await model.checkFirstTime(tool) } }
        .onChange(of: tool) { _, tool in Task { await model.checkFirstTime(tool) } }
        .sheet(isPresented: Binding(
            get: { model[tool].sheet != nil },
            set: { if !$0 { model.cancelConfirm(tool) } }
        )) {
            SessionSheetHost(model: model, tool: tool)
        }
    }

    private func header(_ state: ToolSessions) -> some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            HStack(spacing: 12) {
                ToolTabs(tool: $tool)
                if let list = state.list {
                    Text(SessionText.totals(list)).font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
                }
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    Text(checkedText(state, now: context.date)).font(.system(size: 12)).foregroundStyle(.secondary)
                    Button { Task { await model.refresh(tool) } } label: {
                        Label("다시 계산", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(PillButtonStyle())
                    .disabled(state.checking || model.service == nil)
                }
                .padding(.leading, 12).padding(3)
                .background(GlassPanel(cornerRadius: 17))
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func checkedText(_ state: ToolSessions, now: Date) -> String {
        if state.checking { return "계산 중…" }
        guard let list = state.list else { return "확인 전" }
        return "\(SessionText.relative(list.checkedAt, now: now)) 확인"
    }
}

/// The mockup's `.seg`: two 26 pt buttons in a glass capsule, each with its
/// tool's 7 pt dot; the chosen one is lit.
struct ToolTabs: View {
    @Binding var tool: SessionTool

    var body: some View {
        HStack(spacing: 2) {
            tab(.claude, "Claude Code", dot: Color(red: 0x1f / 255, green: 0x6f / 255, blue: 0xe0 / 255))
            tab(.codex, "Codex", dot: Color(red: 0xd9 / 255, green: 0x70 / 255, blue: 0))
        }
        .padding(3)
        .background(GlassPanel(cornerRadius: 17))
    }

    private func tab(_ value: SessionTool, _ title: String, dot: Color) -> some View {
        let chosen = tool == value
        return Button { tool = value } label: {
            HStack(spacing: 6) {
                Circle().fill(dot).frame(width: 7, height: 7)
                Text(title).font(.system(size: 12, weight: .semibold))
            }
            .padding(.horizontal, 12).frame(height: 26)
            .background { if chosen { GlassPanel(cornerRadius: 13, milk: 0.7, shadowRadius: 0) } }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}

/// The mockup's grid: 22, 18, flexible, 96, 72 and 96 pt, 12 apart.
private enum Grid {
    static let chevron: CGFloat = 22
    static let check: CGFloat = 18
    static let count: CGFloat = 96
    static let size: CGFloat = 72
    static let status: CGFloat = 96
    static let gap: CGFloat = 12
}

/// One tool's screen under the header. `rows`, the selection totals and the
/// rules text are worked out once here, not in each row.
private struct SessionsPane: View {
    let model: SessionsModel
    let tool: SessionTool
    let state: ToolSessions
    let list: SessionList

    var body: some View {
        let rows = SessionRules.rows(list.groups)
        // Once per body: rows count from now, with no timer of their own.
        let now = Date.now
        let totals = SessionRules.selectionTotals(state.selection, in: list)
        VStack(alignment: .leading, spacing: 10) {
            QuickSelectBar(model: model, tool: tool, list: list)
            columnTitles
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(rows) { row in
                        SessionGroupRows(model: model, tool: tool, row: row, state: state, now: now)
                    }
                }
                .padding(.vertical, 3)
            }
            .background(GlassPanel(cornerRadius: 16))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            BottomBar(count: totals.count, bytes: totals.bytes,
                      rule: SessionText.bottomRule(tool: tool, historyDbBytes: list.totals.historyDbBytes),
                      canDelete: totals.count > 0 && model.service != nil && state.work == nil,
                      delete: { model.askToDelete(tool) })
        }
        .padding(EdgeInsets(top: 8, leading: 20, bottom: 18, trailing: 18))
    }

    private var columnTitles: some View {
        HStack(spacing: Grid.gap) {
            Color.clear.frame(width: Grid.chevron)
            Color.clear.frame(width: Grid.check)
            Text("프로젝트 · 세션").frame(maxWidth: .infinity, alignment: .leading)
            Text("세션 수 · 마지막 기록").lineLimit(1).frame(width: Grid.count, alignment: .leading)
            Text("크기").frame(width: Grid.size, alignment: .trailing)
            Text("상태").frame(width: Grid.status, alignment: .leading)
        }
        .font(.system(size: 11.5, weight: .semibold)).foregroundStyle(.secondary)
        .padding(.horizontal, 16)
    }
}

/// "빠르게 고르기" and, on the right, what the tool does by itself (Claude Code)
/// or the leftovers a delete makes (Codex).
private struct QuickSelectBar: View {
    let model: SessionsModel
    let tool: SessionTool
    let list: SessionList

    var body: some View {
        HStack(spacing: 6) {
            Text("빠르게 고르기").font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary).padding(.trailing, 4)
            switch tool {
            case .claude:
                let old = SessionRules.quickSelect(list, olderThanDays: 5, now: .now).count
                quickButton(days: 5) { HStack(spacing: 4) { Text("5일 넘게 기록 없음"); Text("\(old)").foregroundStyle(.secondary).monospacedDigit() } }
            case .codex:
                quickButton(days: 5) { Text("5일 넘게 기록 없음") }
                quickButton(days: 30) { Text("30일 넘게 기록 없음") }
            }
            Button { model.clearSelection(tool) } label: { Text("모두 해제") }
                .buttonStyle(PillButtonStyle(height: 26))
            Spacer(minLength: 0)
            switch tool {
            case .claude: claudeNote
            case .codex: CodexLeftovers(model: model)
            }
        }
        .font(.system(size: 12))
    }

    private func quickButton<Label: View>(days: Int, @ViewBuilder _ label: () -> Label) -> some View {
        Button { model.quickSelect(days: days, tool: tool) } label: { label() }
            .buttonStyle(PillButtonStyle(height: 26))
    }

    private var claudeNote: some View {
        HStack(spacing: 6) {
            Image(systemName: "clock").font(.system(size: 11.5))
            Text("30일이 지난 세션과 남은 데이터는 Claude Code가 알아서 지워요 (Claude 앱 세션 제외)")
        }
        .font(.system(size: 11.5)).foregroundStyle(.secondary)
        .lineLimit(1)
    }
}

/// The orange pill that opens the cleanup of what deleted Codex sessions leave
/// behind.
private struct CodexLeftovers: View {
    let model: SessionsModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "trash").font(.system(size: 12, weight: .semibold))
                .foregroundStyle(ChipStyle.different.foreground(dark: colorScheme == .dark))
            Text("지운 세션이 남긴 데이터").font(.system(size: 12, weight: .semibold))
            Text("Codex를 껐다 켜요").font(.system(size: 11.5)).foregroundStyle(.secondary)
            Button("정리…") { model.openCleanup() }
                .buttonStyle(PillButtonStyle(height: 24))
                .disabled(model.service == nil || model[.codex].work != nil)
        }
        .lineLimit(1)
        .padding(EdgeInsets(top: 2, leading: 10, bottom: 2, trailing: 2))
        .background(GlassPanel(cornerRadius: 15, tint: .orange, tintStrength: 0.12, shadowRadius: 0))
    }
}

/// A group (or the merged row) and, while open, its first five sessions and
/// "나머지 N개 보기". No wrapper view: the `LazyVStack` above lays out each
/// of these rows on its own, so only the ones on screen are built.
private struct SessionGroupRows: View {
    let model: SessionsModel
    let tool: SessionTool
    let row: SessionRules.DisplayRow
    let state: ToolSessions
    /// The time the last records are counted from.
    let now: Date

    var body: some View {
        let open = state.expanded.contains(row.id)
        let selectable = SessionRules.selectable(in: row)
        let picked = state.selection.intersection(selectable)
        GroupRow(row: row, open: open, checkState: SessionRules.checkState(state.selection, among: selectable),
                 canPick: !selectable.isEmpty, pickedCount: picked.count,
                 toggleOpen: { model.toggleExpanded(row.id, in: tool) },
                 toggleAll: { model.toggleGroup(row.id, in: tool) })
        if open {
            let all = state.showAll.contains(row.id)
            let shown = SessionRules.visibleItems(row, showAll: all)
            ForEach(shown) { item in
                ItemRow(item: item, tool: tool, picked: state.selection.contains(item.id), now: now,
                        canRecover: model.service != nil && state.work == nil,
                        toggle: { model.toggle(item.id, in: tool) },
                        recover: { finish in Task { await model.recover(item.id, finish: finish) } })
            }
            if shown.count < row.items.count {
                MoreRow(remaining: row.items.count - shown.count) { model.showAll(row.id, in: tool) }
            }
        }
        Rectangle().fill(Self.line).frame(height: 1).padding(.horizontal, 16)
    }

    private static let line = Color.primary.opacity(0.08)
}

private struct GroupRow: View {
    let row: SessionRules.DisplayRow
    let open: Bool
    let checkState: SessionRules.CheckState
    let canPick: Bool
    let pickedCount: Int
    let toggleOpen: () -> Void
    let toggleAll: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let (title, subtitle) = texts
        HStack(spacing: Grid.gap) {
            Button(action: toggleOpen) {
                Image(systemName: open ? "chevron.down" : "chevron.right")
                    .font(.system(size: 11, weight: .bold))
                    .frame(width: Grid.chevron, height: Grid.chevron)
                    .contentShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(title) \(open ? "접기" : "펼치기")")
            SessionCheckbox(state: checkState, enabled: canPick, label: "\(title) 전체 선택", action: toggleAll)
                .frame(width: Grid.check)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 13.5, weight: .bold)).lineLimit(1)
                // "N개 선택" in blue, as the mockup has it.
                HStack(spacing: 0) {
                    Text(subtitle).foregroundStyle(.secondary)
                    if pickedCount > 0 {
                        Text(" · ").foregroundStyle(.secondary)
                        Text("\(pickedCount)개 선택").bold().foregroundStyle(RailPalette(dark: colorScheme == .dark).accent)
                    }
                }
                .font(.system(size: 11)).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("\(row.sessions)개").font(.system(size: 12.5)).monospacedDigit()
                .frame(width: Grid.count, alignment: .leading)
            Text(SessionText.bytes(row.bytes)).font(.system(size: 12.5, weight: .bold)).monospacedDigit()
                .frame(width: Grid.size, alignment: .trailing)
            Group {
                if row.running > 0 { SessionChip(text: "실행 중 \(row.running)", style: .different, dot: true) }
            }
            .frame(width: Grid.status, alignment: .leading)
        }
        .padding(.horizontal, 16).padding(.vertical, 6)
    }

    /// The name and the line under it.
    private var texts: (String, String) {
        switch row {
        case .group(let group): (group.name, SessionText.groupSubLine(group, selected: 0))
        case .merged(let title, let subtitle, _): (title, subtitle)
        }
    }
}

private struct ItemRow: View {
    let item: SessionItem
    let tool: SessionTool
    let picked: Bool
    let now: Date
    let canRecover: Bool
    let toggle: () -> Void
    /// `finish`: 마저 지우기; otherwise 되돌리기.
    let recover: (_ finish: Bool) -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let chip = SessionText.chip(item)
        HStack(spacing: Grid.gap) {
            Color.clear.frame(width: Grid.chevron)
            SessionCheckbox(state: picked ? .all : .none, enabled: SessionRules.isSelectable(item),
                            label: item.title, action: toggle)
                .frame(width: Grid.check)
            VStack(alignment: .leading, spacing: 1) {
                Text(item.title).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(SessionText.subLine(item, tool: tool)).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                if item.state == "interrupted" {
                    HStack(spacing: 6) {
                        Button("되돌리기") { recover(false) }.buttonStyle(PillButtonStyle(height: 22))
                        Button("마저 지우기") { recover(true) }.buttonStyle(PillButtonStyle(height: 22))
                    }
                    .disabled(!canRecover).padding(.top, 3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(SessionText.relative(item.lastActivity, now: now)).font(.system(size: 12)).foregroundStyle(.secondary)
                .frame(width: Grid.count, alignment: .leading)
            Text(SessionText.bytes(item.bytes)).font(.system(size: 12.5)).monospacedDigit()
                .frame(width: Grid.size, alignment: .trailing)
            Group {
                if let chip {
                    SessionChip(text: chip, style: item.state == "running" ? .different : .unknown,
                                dot: item.state == "running")
                }
            }
            .frame(width: Grid.status, alignment: .leading)
        }
        .padding(.horizontal, 16).padding(.vertical, 6)
        .background(colorScheme == .dark ? Color.white.opacity(0.03) : Color.white.opacity(0.12))
    }
}

private struct MoreRow: View {
    let remaining: Int
    let action: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: Grid.gap) {
            Color.clear.frame(width: Grid.chevron + Grid.gap + Grid.check)
            Button("나머지 \(remaining)개 보기", action: action)
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(RailPalette(dark: colorScheme == .dark).accent)
                .padding(.horizontal, 8).frame(height: 24)
            Spacer(minLength: 0)
        }
        .padding(EdgeInsets(top: 4, leading: 16, bottom: 5, trailing: 16))
        .background(colorScheme == .dark ? Color.white.opacity(0.03) : Color.white.opacity(0.12))
    }
}

/// The 15 pt box of the mockup. A system toggle has no half-checked box in
/// SwiftUI, which a group with some of its sessions picked needs.
private struct SessionCheckbox: View {
    let state: SessionRules.CheckState
    let enabled: Bool
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 15))
                .foregroundStyle(state == .none ? AnyShapeStyle(.secondary)
                                 : AnyShapeStyle(Color(red: 0x0a / 255, green: 0x72 / 255, blue: 0xf2 / 255)))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.35)
        .accessibilityLabel(label)
        .accessibilityValue(state == .all ? "선택됨" : state == .some ? "일부 선택됨" : "선택 안 됨")
    }

    private var symbol: String {
        switch state {
        case .none: "square"
        case .some: "minus.square.fill"
        case .all: "checkmark.square.fill"
        }
    }
}

/// A 20 pt chip in the 설정 menu's colors, with the mockup's dot for "실행 중".
private struct SessionChip: View {
    let text: String
    let style: ChipStyle
    let dot: Bool
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let dark = colorScheme == .dark
        HStack(spacing: 5) {
            if dot { Circle().fill(style.foreground(dark: dark)).frame(width: 6, height: 6) }
            Text(text).lineLimit(1)
        }
        .font(.system(size: 11, weight: .bold))
        .foregroundStyle(style.foreground(dark: dark))
        .padding(.horizontal, 8).frame(height: 20)
        .background(Capsule().fill(style.background(dark: dark)))
    }
}

/// "N개 선택 · X GB", the rule, and the red button, off with nothing picked.
private struct BottomBar: View {
    let count: Int
    let bytes: Int64
    let rule: String
    let canDelete: Bool
    let delete: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Text(SessionText.selectionLine(count: count, bytes: bytes))
                .font(.system(size: 13, weight: .bold)).monospacedDigit()
            Text(rule).font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: delete) { Label("선택 지우기…", systemImage: "trash") }
                .buttonStyle(DeleteButtonStyle())
                .disabled(!canDelete)
        }
        .padding(EdgeInsets(top: 6, leading: 18, bottom: 6, trailing: 6))
        .background(GlassPanel(cornerRadius: 22))
    }
}

/// The red pill of the mockup's "선택 지우기…".
struct DeleteButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .labelStyle(.titleAndIcon)
            .font(.system(size: 12.5, weight: .semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 16).frame(height: 32)
            .background(
                Capsule().fill(LinearGradient(colors: [Color(red: 1, green: 0x6a / 255, blue: 0x5f / 255),
                                                       Color(red: 0xd4 / 255, green: 0x30 / 255, blue: 0x2a / 255)],
                                              startPoint: .top, endPoint: .bottom))
                    .shadow(color: Color(red: 0xd4 / 255, green: 0x30 / 255, blue: 0x2a / 255).opacity(0.3), radius: 5, y: 3))
            .contentShape(Capsule())
            .opacity(configuration.isPressed ? 0.8 : isEnabled ? 1 : 0.4)
    }
}
