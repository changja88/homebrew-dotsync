import AppKit
import DotsyncKit
import SwiftUI

struct AccountsWindow: View {
    @Bindable var model: AccountsModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(spacing: 0) {
                if let message = model.message {
                    HStack {
                        Text(message).font(.callout)
                        Spacer()
                        Button { model.message = nil } label: { Image(systemName: "xmark") }
                            .buttonStyle(.borderless)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .glassEffect(.regular.tint(.orange.opacity(0.3)), in: .rect(cornerRadius: 12))
                    .padding(.horizontal, 12).padding(.top, 8)
                }
                ScrollView {
                    GlassEffectContainer(spacing: 12) {
                        VStack(alignment: .leading, spacing: 12) {
                            if let active = model.file.activeAccount {
                                ActiveAccountCard(account: active, now: context.date, model: model)
                            } else if let seat = model.file.unsavedSeat {
                                UnsavedSeatCard(seat: seat, now: context.date, model: model)
                            }
                            let others = model.accounts.filter { $0.name != model.file.active }
                            if !others.isEmpty {
                                let hasCard = model.file.activeAccount != nil || model.file.unsavedSeat != nil
                                ColumnHeader(title: "\(hasCard ? "다른 계정" : "계정") \(others.count)")
                                VStack(spacing: 0) {
                                    ForEach(Array(others.enumerated()), id: \.element.id) { index, account in
                                        if index > 0 { Divider().padding(.horizontal, 16) }
                                        AccountRow(account: account, now: context.date, model: model)
                                    }
                                }
                                .padding(.vertical, 4)
                                .glassEffect(.regular, in: .rect(cornerRadius: 18))
                            }
                            if model.file.accounts.isEmpty && model.file.unsavedSeat == nil {
                                Text("저장된 계정이 없어요 — ‘계정 추가’로 시작하세요")
                                    .foregroundStyle(.secondary).padding(40)
                                    .frame(maxWidth: .infinity)
                            }
                        }
                    }
                    .padding(.horizontal, 18).padding(.top, 8).padding(.bottom, 18)
                }
            }
            .toolbar {
                ToolbarItem {
                    Text(model.running ?? "\(UsageText.ago(model.file.fetchedAt, now: context.date)) 갱신")
                        .font(.callout).foregroundStyle(.secondary)
                }
                ToolbarItem {
                    Button("새로고침", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
                        .labelStyle(.titleAndIcon)
                        .disabled(model.isBusy)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button("계정 추가", systemImage: "plus") { model.sheet = .add(thenUse: nil) }
                        .labelStyle(.titleAndIcon)
                        .disabled(model.isBusy)
                }
            }
        }
        .frame(minWidth: 760, minHeight: 420)
        // Looks the same focused or not: macOS would grey the blue buttons and
        // turn the see-through ground flat in a window that isn't in front.
        .environment(\.appearsActive, true)
        // The desktop shows through, so the glass rows have something to refract.
        .containerBackground(for: .window) { ActiveBackdrop() }
        .sheet(item: $model.sheet) { sheet in
            AccountSheet(sheet: sheet, model: model)
        }
        .confirmationDialog(
            "계정을 삭제할까요?",
            isPresented: Binding(get: { model.pendingRemoval != nil },
                                 set: { if !$0 { model.pendingRemoval = nil } }),
            presenting: model.pendingRemoval
        ) { account in
            Button("삭제", role: .destructive) { Task { await model.remove(account.name) } }
            Button("취소", role: .cancel) {}
        } message: { account in
            Text("이 맥에서 \(account.label)의 로그인이 지워져요.")
        }
        .task { await model.refresh() }
    }
}

/// "다른 계정 6 · 5시간 · 주간" over the list, on the rows' columns.
struct ColumnHeader: View {
    let title: String

    var body: some View {
        HStack(spacing: AccountColumns.spacing) {
            Text(title).frame(width: AccountColumns.name, alignment: .leading)
            Text("5시간").frame(maxWidth: .infinity, alignment: .leading)
            Text("주간").frame(maxWidth: .infinity, alignment: .leading)
            Color.clear.frame(width: AccountColumns.actions, height: 1)
        }
        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
        .padding(.horizontal, 16)
    }
}

/// Claude is on a login no saved account holds: the card says so and offers
/// to save it.
struct UnsavedSeatCard: View {
    let seat: UnsavedSeat
    let now: Date
    let model: AccountsModel

    var body: some View {
        HStack(spacing: 24) {
            VStack(alignment: .leading, spacing: 4) {
                Text("사용 중 · 저장 안 됨")
                    .font(.caption2.weight(.bold)).foregroundStyle(.white)
                    .padding(.horizontal, 9).padding(.vertical, 3)
                    .glassEffect(.regular.tint(.orange), in: .capsule)
                Text(seat.email ?? "알 수 없는 계정").font(.title3.weight(.bold)).lineLimit(1).padding(.top, 4)
            }
            .frame(width: 190, alignment: .leading)
            RingMetricView(title: "5시간", window: seat.fiveHour, now: now)
            RingMetricView(title: "주간", window: seat.sevenDay, now: now)
            Button("저장하기…") { model.sheet = .add(thenUse: nil) }
                .buttonStyle(.glassProminent)
                .disabled(model.isBusy)
        }
        .padding(18)
        .glassEffect(.regular.tint(.blue.opacity(0.25)), in: .rect(cornerRadius: 20))
    }
}

/// The window's see-through ground, kept in its active look when the window
/// is not in front (a SwiftUI material follows the window's state).
struct ActiveBackdrop: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .underWindowBackground
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
}
