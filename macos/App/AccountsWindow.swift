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
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .background(.orange.opacity(0.15))
                }
                ScrollView {
                    VStack(spacing: 4) {
                        if model.file.active == nil, let seat = model.file.unsavedSeat {
                            UnsavedSeatRow(seat: seat, now: context.date, model: model)
                        }
                        ForEach(model.accounts) { account in
                            AccountRow(account: account, isActive: account.name == model.file.active,
                                       now: context.date, model: model)
                        }
                        if model.file.accounts.isEmpty && model.file.unsavedSeat == nil {
                            Text("저장된 계정이 없어요 — ‘계정 추가’로 시작하세요")
                                .foregroundStyle(.secondary).padding(40)
                        }
                    }
                    .padding(12)
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

/// Claude is on a login no saved account holds.
struct UnsavedSeatRow: View {
    let seat: UnsavedSeat
    let now: Date
    let model: AccountsModel

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(seat.email ?? "알 수 없는 계정").font(.body.weight(.semibold)).lineLimit(1)
                Text("사용 중 · 저장 안 됨").font(.caption).foregroundStyle(.orange)
            }
            .frame(width: 180, alignment: .leading)
            MetricView(title: "5시간", window: seat.fiveHour, now: now)
            MetricView(title: "주간", window: seat.sevenDay, now: now)
            Button("저장하기…") { model.sheet = .add(thenUse: nil) }
                .disabled(model.isBusy)
                .frame(width: 150, alignment: .trailing)
        }
        .padding(10)
        .background(Color.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}
