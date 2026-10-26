import DotsyncKit
import SwiftUI

/// The Codex cleanup (`F-codex-cleanup`): what to know before ChatGPT is
/// quit, what gets cleaned, what stays, then the run and its result. The rows,
/// the button's state and the summary come from `CleanupRules`.
struct CleanupSheet: View {
    let model: SessionsModel

    var body: some View {
        Group {
            switch model.cleanup.phase {
            case .choosing: ChoosingView(model: model, state: model.cleanup)
            case .running: RunningView(model: model, work: model[.codex].work)
            case .finished(let summary): FinishedView(model: model, summary: summary)
            }
        }
        .padding(EdgeInsets(top: 18, leading: 22, bottom: 16, trailing: 22))
        .frame(width: 620)
        .interactiveDismissDisabled(!isChoosing)
    }

    private var isChoosing: Bool {
        if case .choosing = model.cleanup.phase { return true }
        return false
    }
}

// MARK: Choosing

private struct ChoosingView: View {
    let model: SessionsModel
    let state: CleanupState

    var body: some View {
        let items = state.preview.map { CleanupRules.visibleItems($0.items) } ?? []
        let start = state.blockers.map {
            CleanupRules.startState(blockers: $0, preview: state.preview, selection: state.selection, vacuum: state.vacuum)
        } ?? (allowed: false, note: nil)
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Codex를 껐다 켜고 남은 데이터를 정리할까요?").font(.system(size: 17, weight: .bold))
                Text("지운 세션이 남긴 데이터예요. 정리하는 동안 ChatGPT 앱이 꺼졌다가 다시 켜져요.")
                    .font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
            CleanupBox(title: "시작하기 전에") { startRows }
            CleanupBox(title: "정리할 것") { itemRows(items) }
            CleanupBox(title: "그대로 두는 것", secondary: true) {
                ForEach(Array(SessionText.cleanupKept.enumerated()), id: \.offset) { index, kept in
                    if index > 0 { RowDivider() }
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(kept.name).frame(maxWidth: .infinity, alignment: .leading)
                        Text(kept.reason).foregroundStyle(.secondary).frame(width: 236, alignment: .leading)
                    }
                    .font(.system(size: 12)).padding(.vertical, 5)
                }
            }
            if let preview = state.preview,
               let box = CleanupRules.vacuumBox(preview: preview, selection: state.selection, ticked: state.vacuum) {
                Toggle(isOn: Binding(get: { box.ticked }, set: { model.cleanup.vacuum = $0 })) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("정리 뒤 대화 저장소의 빈 공간 돌려받기").font(.system(size: 12.5, weight: .semibold))
                        Text(box.hint).font(.system(size: 11.5)).foregroundStyle(.secondary)
                    }
                }
                .toggleStyle(.checkbox)
                .disabled(!box.enabled)
                .padding(.horizontal, 2).padding(.top, 2)
            }
            HStack(spacing: 8) {
                if let note = start.note {
                    Text(note).font(.system(size: 11.5)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                Button("취소") { model.closeCleanup() }
                    .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5, sidePadding: 16))
                    .keyboardShortcut(.cancelAction)
                Button("끄고 정리하기") { Task { await model.startCleanup() } }
                    .buttonStyle(DeleteButtonStyle(fontSize: 13, sidePadding: 18))
                    .keyboardShortcut(.defaultAction)
                    .disabled(!start.allowed)
            }
            .padding(.top, 2)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private var startRows: some View {
        if let blockers = state.blockers {
            let rows = CleanupRules.startRows(blockers)
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                if index > 0 { RowDivider() }
                StartRowView(row: row, recheck: model.recheckCleanup)
            }
        } else if let failure = state.failure {
            failureLine(failure)
        } else {
            readingLine("실행 중인 Codex를 확인하는 중…")
        }
    }

    @ViewBuilder private func itemRows(_ items: [CleanupItem]) -> some View {
        if let preview = state.preview {
            if let refusal = preview.refusal {
                failureLine(SessionText.cleanupRefusal(refusal))
            } else if items.isEmpty {
                Text("정리할 것이 없어요").font(.system(size: 12)).foregroundStyle(.secondary).padding(.vertical, 5)
            }
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                if index > 0 { RowDivider() }
                HStack(spacing: 10) {
                    Toggle("", isOn: Binding(get: { state.selection.contains(item.id) }, set: { _ in model.toggleCleanupItem(item.id) }))
                        .toggleStyle(.checkbox).labelsHidden().frame(width: 18)
                        .disabled(item.count == 0)
                    HStack(spacing: 6) {
                        Text(item.label)
                        if item.id == "history_rows" {
                            Text(SessionText.historyDbName).font(.system(size: 10.5, design: .monospaced)).foregroundStyle(.secondary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    // The first item, the store itself, is the one to look at.
                    Text(CleanupRules.countText(item)).fontWeight(item.id == "history_rows" ? .semibold : .regular)
                        .monospacedDigit().frame(width: 150, alignment: .leading)
                }
                .font(.system(size: 12)).padding(.vertical, 5)
            }
        } else if let failure = state.failure {
            failureLine(failure)
        } else {
            VStack(alignment: .leading, spacing: 6) {
                if let progress = state.readProgress {
                    ProgressView(value: Double(min(progress.done, progress.total)), total: Double(progress.total))
                    Text(SessionText.progressLine(phase: "read", done: progress.done, total: progress.total))
                        .font(.system(size: 12)).foregroundStyle(.secondary).monospacedDigit()
                } else {
                    readingLine("남은 데이터를 찾는 중…")
                }
            }
            .padding(.vertical, 5)
        }
    }

    private func failureLine(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true).padding(.vertical, 5)
    }

    private func readingLine(_ text: String) -> some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(text).font(.system(size: 12)).foregroundStyle(.secondary)
        }
        .padding(.vertical, 5)
    }
}

/// One line of 시작하기 전에: a round mark, the name (112 pt), what happens,
/// and 다시 확인 where something has to end first.
private struct StartRowView: View {
    let row: CleanupRules.StartRow
    let recheck: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let warns = row.style != .ok
        let style: ChipStyle = warns ? .different : .unknown
        let dark = colorScheme == .dark
        HStack(spacing: 10) {
            Image(systemName: row.style == .ok ? "checkmark" : "exclamationmark")
                .font(.system(size: 9, weight: .heavy))
                .foregroundStyle(style.foreground(dark: dark))
                .frame(width: 18, height: 18)
                .background(Circle().fill(style.background(dark: dark)))
            Text(row.title).font(.system(size: 12, weight: warns ? .bold : .semibold))
                .foregroundStyle(warns ? AnyShapeStyle(style.foreground(dark: dark)) : AnyShapeStyle(.primary))
                .frame(width: 112, alignment: .leading)
            Text(row.detail).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            if row.canRecheck {
                Button("다시 확인", action: recheck).buttonStyle(PillButtonStyle(height: 24, shadowRadius: 1.5, fontSize: 12, sidePadding: 10))
            }
        }
        .font(.system(size: 12)).padding(.vertical, 6)
    }
}

// MARK: Running and finished

private struct RunningView: View {
    let model: SessionsModel
    let work: SessionWork?
    @State private var stopping = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(work?.label ?? "Codex 정리 중…").font(.system(size: 17, weight: .bold))
                Text("ChatGPT 앱이 꺼졌다가 정리가 끝나면 다시 켜져요. 끝나면 결과를 보여 드려요.")
                    .font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
            if let progress = work?.progress, progress.hasCount {
                // The vacuum's percent is the line itself; the bar shows it too.
                ProgressView(value: Double(min(progress.done, progress.total)), total: Double(progress.total))
                Text(progress.text)
                    .font(.system(size: 12.5, weight: .semibold)).monospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .trailing).padding(.top, -8)
            } else {
                ProgressView().progressViewStyle(.linear)
            }
            HStack {
                Spacer()
                Button(stopping ? "멈추는 중…" : "멈추기") {
                    stopping = true
                    model.stop(.codex)
                }
                .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
                .disabled(stopping)
            }
        }
    }
}

private struct FinishedView: View {
    let model: SessionsModel
    let summary: CleanupRules.Summary
    @State private var relaunching = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(summary.title).font(.system(size: 17, weight: .bold))
                ForEach(summary.notes, id: \.self) {
                    Text($0).font(.system(size: 12.5)).foregroundStyle(.secondary)
                }
            }
            if !summary.rows.isEmpty {
                CleanupBox(title: CleanupRules.resultsHeading) {
                    ForEach(Array(summary.rows.enumerated()), id: \.offset) { index, row in
                        if index > 0 { RowDivider() }
                        HStack(spacing: 10) {
                            ResultMark(mark: row.mark)
                            Text(row.label).frame(maxWidth: .infinity, alignment: .leading)
                            Text(row.text).foregroundStyle(.secondary)
                        }
                        .font(.system(size: 12)).padding(.vertical, 5)
                    }
                }
            }
            if !summary.vacuumLines.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(summary.vacuumLines, id: \.self) {
                        Text($0).font(.system(size: 12.5)).foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal, 2)
            }
            HStack(spacing: 8) {
                Spacer()
                if summary.relaunchFailed {
                    Button(relaunching ? "여는 중…" : "ChatGPT 다시 켜기") {
                        relaunching = true
                        Task {
                            _ = await model.relaunchChatGPT()
                            relaunching = false
                        }
                    }
                    .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
                    .disabled(relaunching)
                }
                Button("확인") { model.closeCleanup() }
                    .buttonStyle(PillButtonStyle(prominent: true, height: 32))
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 2)
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// ✓ in green for an item that ran, a grey dash for one that was skipped, ! in
/// orange for one that failed.
private struct ResultMark: View {
    let mark: CleanupRules.ResultRow.Mark
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let (style, symbol): (ChipStyle, String) = switch mark {
        case .done: (.same, "checkmark")
        case .skipped: (.unknown, "minus")
        case .failed: (.different, "exclamationmark")
        }
        let dark = colorScheme == .dark
        Image(systemName: symbol)
            .font(.system(size: 9, weight: .heavy))
            .foregroundStyle(style.foreground(dark: dark))
            .frame(width: 18, height: 18)
            .background(Circle().fill(style.background(dark: dark)))
    }
}

// MARK: Pieces

/// The mockup's `.box`: a 12 pt bold heading, then rows split by a hairline.
private struct CleanupBox<Content: View>: View {
    let title: String
    var secondary = false
    @ViewBuilder var content: Content

    var body: some View {
        ChangeBox(spacing: 0, padded: false) {
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(.system(size: 12, weight: .bold))
                    .foregroundStyle(secondary ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
                    .padding(.top, 5).padding(.bottom, 4)
                // The mockup's rows each start with a line, the first one too.
                RowDivider()
                content
            }
            .padding(.horizontal, 14).padding(.vertical, 6)
        }
    }
}

private struct RowDivider: View {
    var body: some View {
        Rectangle().fill(.primary.opacity(0.1)).frame(height: 1)
    }
}
