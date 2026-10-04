import DotsyncKit
import SwiftUI

/// The window, as the approved mockup "앱 A": the account in use on top in a
/// tinted glass card, the others in one glass list under column titles.
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
                    .background(GlassPanel(cornerRadius: 12, tint: .orange, shadow: false))
                    .padding(.horizontal, 18).padding(.top, 8)
                }
                ScrollView {
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
                                    if index > 0 {
                                        Rectangle().fill(.primary.opacity(0.08)).frame(height: 1)
                                            .padding(.horizontal, 16)
                                    }
                                    AccountRow(account: account, now: context.date, model: model)
                                }
                            }
                            .padding(.vertical, 4)
                            .background(GlassPanel(cornerRadius: 18))
                        }
                        if model.file.accounts.isEmpty && model.file.unsavedSeat == nil {
                            Text("저장된 계정이 없어요 — ‘계정 추가’로 시작하세요")
                                .foregroundStyle(.secondary).padding(40)
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .padding(.horizontal, 18).padding(.top, 8).padding(.bottom, 18)
                }
            }
            .toolbar(removing: .title)
            .toolbar {
                ToolbarItem(placement: .navigation) {
                    Text("Claude 계정").font(.system(size: 15, weight: .bold)).padding(.leading, 8)
                }
                .sharedBackgroundVisibility(.hidden)
                ToolbarItem(placement: .primaryAction) {
                    HStack(spacing: 12) {
                        HStack(spacing: 8) {
                            Text(model.running ?? "\(UsageText.ago(model.file.fetchedAt, now: context.date)) 갱신")
                                .font(.system(size: 12)).foregroundStyle(.secondary)
                            Button { Task { await model.refresh() } } label: {
                                Label("새로고침", systemImage: "arrow.clockwise")
                            }
                            .buttonStyle(PillButtonStyle())
                            .disabled(model.isBusy)
                        }
                        .padding(.leading, 12).padding(3)
                        .background(GlassPanel(cornerRadius: 17))
                        Button { model.sheet = .add(thenUse: nil) } label: {
                            Label("계정 추가", systemImage: "plus")
                        }
                        .buttonStyle(PillButtonStyle(prominent: true, height: 34))
                        .disabled(model.isBusy)
                    }
                    .padding(.trailing, 2)
                }
                .sharedBackgroundVisibility(.hidden)
            }
        }
        .frame(minWidth: 760, minHeight: 420)
        // Looks the same focused or not: macOS would dim what follows the
        // window's state in a window that isn't in front.
        .environment(\.appearsActive, true)
        // The desktop shows through, behind the glass panels.
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
        .font(.system(size: 11.5, weight: .semibold)).foregroundStyle(.secondary)
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
                BlueBadge(text: "사용 중 · 저장 안 됨", color: .orange)
                Text(seat.email ?? "알 수 없는 계정").font(.system(size: 18, weight: .bold))
                    .lineLimit(1).padding(.top, 4)
            }
            .frame(width: 190, alignment: .leading)
            RingMetricView(title: "5시간", window: seat.fiveHour, now: now)
            RingMetricView(title: "주간", window: seat.sevenDay, now: now)
            Button("저장하기…") { model.sheet = .add(thenUse: nil) }
                .buttonStyle(PillButtonStyle(prominent: true))
                .disabled(model.isBusy)
        }
        .padding(.horizontal, 20).padding(.vertical, 18)
        .background(GlassPanel(cornerRadius: 20, tint: .blue))
    }
}
