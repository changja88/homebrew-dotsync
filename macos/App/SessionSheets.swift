import DotsyncKit
import SwiftUI

/// The sheet over the 세션 menu while a delete is being decided, run, or
/// reported: the confirm sheet (`F-claude-confirm`, `F-codex-confirm`), the
/// run's progress, and the result (`F-codex-result`). All of them say only what
/// the CLI reported.
struct SessionSheetHost: View {
    let model: SessionsModel
    let tool: SessionTool

    /// The cleanup sheet is Task 21's.
    static func presents(_ sheet: SessionSheet) -> Bool {
        if case .cleanup = sheet { return false }
        return true
    }

    var body: some View {
        let state = model[tool]
        Group {
            switch state.sheet {
            case .confirm:
                if let list = state.list {
                    ConfirmSheet(model: model, tool: tool, list: list, selection: state.selection)
                }
            case .running:
                RunningSheet(model: model, tool: tool, work: state.work)
            case .result(let report):
                ResultSheet(model: model, report: report, list: state.list)
            case .failure(let message):
                FailureSheet(model: model, tool: tool, message: message)
            case .cleanup, nil:
                EmptyView()
            }
        }
        // The system sheet is clear glass: the list showed through the title.
        .presentationBackground { SheetBackdrop() }
    }
}

// MARK: Confirm

private struct ConfirmSheet: View {
    let model: SessionsModel
    let tool: SessionTool
    let list: SessionList
    let selection: Set<String>

    var body: some View {
        // The ids that would be sent: the same ones `confirmDelete` sends.
        let counts = SessionDeleteRules.confirmCounts(SessionRules.pruned(selection, in: list), in: list)
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(SessionText.confirmTitle(tool: tool, count: counts.count)).font(.system(size: 17, weight: .bold))
                Text(SessionText.confirmSizeLine(tool: tool, children: counts.children, bytes: counts.bytes))
                    .font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
            ChangeBox {
                ForEach(SessionText.confirmRows(tool: tool, children: counts.children), id: \.label) { row in
                    ConfirmLine(row: row, labelWidth: tool == .claude ? 72 : 92)
                }
            }
            InfoStrip(text: SessionText.confirmInfo(tool: tool))
            if let finePrint = SessionText.confirmFinePrint(tool: tool) {
                Text(finePrint).font(.system(size: 11.5)).foregroundStyle(.secondary).padding(.horizontal, 2)
            }
            Text(SessionText.confirmNote(tool: tool, historyDbBytes: list.totals.historyDbBytes))
                .font(.system(size: 11.5)).foregroundStyle(.secondary).padding(.horizontal, 2)
            HStack(spacing: 8) {
                Spacer()
                Button("취소") { model.cancelConfirm(tool) }
                    .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
                    .keyboardShortcut(.cancelAction)
                Button("\(counts.count)개 지우기") { Task { await model.confirmDelete(tool) } }
                    .buttonStyle(DeleteButtonStyle())
                    .keyboardShortcut(.defaultAction)
                    .disabled(counts.count == 0)
            }
            .padding(.top, 2)
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(EdgeInsets(top: 20, leading: 22, bottom: 18, trailing: 22))
        .frame(width: 600)
    }
}

/// One line of the box: its name, the pills, and the reason under them.
private struct ConfirmLine: View {
    let row: SessionText.ConfirmRow
    let labelWidth: CGFloat

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(row.label).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                .frame(width: labelWidth, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                PillFlow(spacing: CGSize(width: 5, height: 4)) {
                    ForEach(row.pills, id: \.self) { Pill(text: $0, keeps: row.kind == .keeps) }
                }
                if let note = row.note {
                    Text(note).font(.system(size: 11.5)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// The mockup's `.pills span`: 20 pt, grey fill; what stays has only a line.
private struct Pill: View {
    let text: String
    let keeps: Bool

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
        Text(text)
            .font(.system(size: 11.5, weight: keeps ? .medium : .semibold))
            .foregroundStyle(keeps ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
            .padding(.horizontal, 8).frame(height: 20)
            .background {
                if keeps {
                    shape.strokeBorder(.primary.opacity(0.1), lineWidth: 1)
                } else {
                    shape.fill(.primary.opacity(0.1))
                }
            }
    }
}

/// Subviews side by side, wrapping to the next line when the width is used.
private struct PillFlow: Layout {
    var spacing: CGSize

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(width: proposal.width ?? .infinity, subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let placed = arrange(width: bounds.width, subviews)
        for (subview, origin) in zip(subviews, placed.origins) {
            subview.place(at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y), proposal: .unspecified)
        }
    }

    private func arrange(width: CGFloat, _ subviews: Subviews) -> (size: CGSize, origins: [CGPoint]) {
        var origins: [CGPoint] = []
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0, widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += lineHeight + spacing.height
                lineHeight = 0
            }
            origins.append(CGPoint(x: x, y: y))
            x += size.width + spacing.width
            lineHeight = max(lineHeight, size.height)
            widest = max(widest, x - spacing.width)
        }
        return (CGSize(width: widest, height: y + lineHeight), origins)
    }
}

/// The blue strip with the circled "i".
private struct InfoStrip: View {
    let text: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let palette = ChipStyle.newerHere
        let dark = colorScheme == .dark
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle").font(.system(size: 13, weight: .semibold)).padding(.top, 1)
            Text(text).lineSpacing(2).fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 12, weight: .semibold))
        .foregroundStyle(palette.foreground(dark: dark))
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(palette.background(dark: dark)))
    }
}

// MARK: Running

private struct RunningSheet: View {
    let model: SessionsModel
    let tool: SessionTool
    let work: SessionWork?
    @State private var stopping = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(work?.label ?? "지우는 중…").font(.system(size: 17, weight: .bold))
                Text("끝나면 결과를 보여 드려요.").font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
            if let work, work.total > 0 {
                ProgressView(value: Double(min(work.done, work.total)), total: Double(work.total))
                HStack {
                    Spacer()
                    Text(SessionText.progress(done: work.done, total: work.total))
                        .font(.system(size: 12.5, weight: .semibold)).monospacedDigit()
                }
                .padding(.top, -8)
            } else {
                ProgressView().progressViewStyle(.linear)
            }
            HStack {
                Spacer()
                if work?.stoppable != false {
                    Button(stopping ? "멈추는 중…" : "멈추기") {
                        stopping = true
                        model.stop(tool)
                    }
                    .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
                    .disabled(stopping)
                }
            }
        }
        .padding(EdgeInsets(top: 20, leading: 22, bottom: 18, trailing: 22))
        .frame(width: 580)
        .interactiveDismissDisabled()
    }
}

// MARK: Result

private struct ResultSheet: View {
    let model: SessionsModel
    let report: DeleteReport
    let list: SessionList?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(SessionText.resultTitle(report)).font(.system(size: 17, weight: .bold))
                if let freed = SessionText.freedLine(SessionDeleteRules.freedBytes(report)) {
                    Text(freed).font(.system(size: 12.5)).foregroundStyle(.secondary)
                }
            }
            ScrollView {
                ChangeBox(spacing: 0, padded: false) {
                    ForEach(Array(report.results.enumerated()), id: \.offset) { index, result in
                        if index > 0 { Divider() }
                        ResultRow(model: model, report: report, result: result,
                                  title: SessionDeleteRules.rowTitle(result.id, in: list))
                    }
                }
            }
            .frame(maxHeight: 380)
            .fixedSize(horizontal: false, vertical: true)
            if report.tool == .codex {
                Text("지운 세션의 입력 기록과 대화 저장소의 빈 공간은 \"남은 데이터 정리\"에서 정리할 수 있어요.")
                    .font(.system(size: 11.5)).foregroundStyle(.secondary).padding(.horizontal, 2)
            }
            HStack(spacing: 8) {
                Spacer()
                if report.tool == .codex {
                    // Task 21 opens the cleanup sheet.
                    Button("남은 데이터 정리…") {}
                        .buttonStyle(PillButtonStyle(height: 32, shadowRadius: 1.5))
                        .disabled(true)
                }
                Button("확인") { Task { await model.dismissResult(report.tool) } }
                    .buttonStyle(PillButtonStyle(prominent: true, height: 32))
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 4)
        }
        .padding(EdgeInsets(top: 20, leading: 22, bottom: 18, trailing: 22))
        .frame(width: 580)
        .interactiveDismissDisabled()
    }
}

private struct ResultRow: View {
    let model: SessionsModel
    let report: DeleteReport
    let result: DeleteResult
    let title: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Mark(outcome: result.outcome)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(line).font(.system(size: 12)).foregroundStyle(.secondary)
                    .lineSpacing(1).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if SessionDeleteRules.canRetry(result) {
                Button("다시 시도") { Task { await model.retry(result.id, in: report) } }
                    .buttonStyle(PillButtonStyle(height: 28, shadowRadius: 1.5))
                    .disabled(model.service == nil)
                    .padding(.vertical, -2).frame(maxHeight: .infinity, alignment: .center)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
    }

    /// The size first, when the CLI reported one, then what happened.
    private var line: String {
        let text = SessionText.resultLine(result, tool: report.tool)
        guard let freed = result.freedBytes, freed > 0 else { return text }
        return "\(SessionText.bytes(freed)) · \(text)"
    }
}

/// The 24 pt round mark: a check for what was deleted (or restored), "!" for
/// a part, an arrow for what was left alone.
private struct Mark: View {
    let outcome: String
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let dark = colorScheme == .dark
        let style = self.style
        Image(systemName: symbol)
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(style.foreground(dark: dark))
            .frame(width: 24, height: 24)
            .background(Circle().fill(style.background(dark: dark)))
            .accessibilityLabel(label)
    }

    private var style: ChipStyle {
        switch outcome {
        case "deleted", "restored": .same
        case "partial": .different
        default: .unknown
        }
    }

    private var symbol: String {
        switch outcome {
        case "deleted", "restored": "checkmark"
        case "partial": "exclamationmark"
        default: "arrow.right"
        }
    }

    private var label: String {
        switch outcome {
        case "deleted": "지움"
        case "restored": "되돌림"
        case "partial": "일부만 지움"
        default: "건너뜀"
        }
    }
}

// MARK: Failure

/// A delete that threw: busy, a CLI error or a timeout. What it did before
/// that is not known here, so the sheet says nothing about it; 다시 계산 reads
/// the list again.
private struct FailureSheet: View {
    let model: SessionsModel
    let tool: SessionTool
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("끝내지 못했어요").font(.system(size: 17, weight: .bold))
            Text(message).font(.system(size: 12.5)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("확인") { Task { await model.dismissResult(tool) } }
                    .buttonStyle(PillButtonStyle(prominent: true, height: 32))
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 4)
        }
        .padding(EdgeInsets(top: 20, leading: 22, bottom: 18, trailing: 22))
        .frame(width: 580)
        .interactiveDismissDisabled()
    }
}
