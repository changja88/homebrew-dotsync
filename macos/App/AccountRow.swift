import DotsyncKit
import SwiftUI

struct AccountRow: View {
    let account: AccountUsage
    let isActive: Bool
    let now: Date
    let model: AccountsModel

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(account.label).font(.body.weight(.semibold)).lineLimit(1)
                    if isActive {
                        Text("사용 중")
                            .font(.caption2.weight(.semibold)).foregroundStyle(.white)
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(Capsule().fill(.blue))
                    }
                }
                Text(account.email ?? account.name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if account.status == .error {
                    Text(account.fetchedAt.map { "\(UsageText.ago($0, now: now)) 값 · 조회 실패" } ?? "조회 실패")
                        .font(.caption2).foregroundStyle(.orange)
                }
            }
            .frame(width: 180, alignment: .leading)
            usage
            actions.frame(width: 150, alignment: .trailing)
        }
        .padding(10)
        .background(isActive ? Color.blue.opacity(0.08) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var usage: some View {
        switch WidgetLayout.kind(of: account, now: now) {
        case .loginLost:
            Text("로그인이 풀렸어요").foregroundStyle(.orange)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .fullWeek(let reset):
            Text("주간 한도 다 씀 · \(Text(UsageText.resetDate(reset)).bold()) 초기화")
                .frame(maxWidth: .infinity, alignment: .leading)
        case .metrics:
            MetricView(title: "5시간", window: account.fiveHour, now: now)
            MetricView(title: "주간", window: account.sevenDay, now: now)
        }
    }

    private var actions: some View {
        HStack(spacing: 6) {
            switch WidgetLayout.kind(of: account, now: now) {
            case .loginLost:
                Button("다시 로그인") { model.sheet = .relogin(account) }
            case .metrics where !isActive:
                Button("사용") { Task { await model.use(account.name) } }
                    .buttonStyle(.borderedProminent)
            default:
                EmptyView()
            }
            Menu {
                Button("이름 바꾸기…") { model.sheet = .rename(account) }
                Button("다시 로그인") { model.sheet = .relogin(account) }
                Divider()
                Button("삭제…", role: .destructive) { model.pendingRemoval = account }
                    .disabled(isActive)
            } label: {
                Image(systemName: "ellipsis")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .disabled(model.isBusy)
    }
}

/// One window of usage: bar, big percent, reset text. A percent whose reset
/// passed is dimmed until the next refresh.
struct MetricView: View {
    let title: String
    let window: UsageWindow?
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    UsageBar(percent: window?.percent ?? 0)
                    Text(UsageText.remaining(until: window?.resetsAt, now: now))
                        .font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
                Text(window.map { "\($0.percent)%" } ?? "—")
                    .font(.title3.weight(.semibold)).monospacedDigit()
                    .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
