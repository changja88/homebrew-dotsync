import DotsyncKit
import SwiftUI

/// The window's columns (approved mockup "앱 A"): name, 5시간, 주간, actions.
enum AccountColumns {
    static let name: CGFloat = 200
    static let actions: CGFloat = 132
    static let spacing: CGFloat = 20
}

/// The account in use, on top in a blue-tinted glass card with two rings.
struct ActiveAccountCard: View {
    let account: AccountUsage
    let now: Date
    let model: AccountsModel

    var body: some View {
        HStack(spacing: 24) {
            VStack(alignment: .leading, spacing: 4) {
                BlueBadge(text: "사용 중")
                Text(account.label).font(.system(size: 24, weight: .bold)).lineLimit(1).padding(.top, 4)
                Text(account.email ?? account.name).font(.system(size: 12.5)).foregroundStyle(.secondary)
                    .lineLimit(1)
                StaleNote(account: account, now: now)
            }
            .frame(width: 190, alignment: .leading)
            switch WidgetLayout.kind(of: account, now: now) {
            case .loginLost:
                Label("로그인이 풀렸어요", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .fullWeek(let reset):
                Text("주간 한도 다 씀 · \(Text(UsageText.resetDate(reset)).bold()) 초기화")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .metrics:
                RingMetricView(title: "5시간", window: account.fiveHour, now: now)
                RingMetricView(title: "주간", window: account.sevenDay, now: now)
            }
            AccountActions(account: account, isActive: true, now: now, model: model, menuSize: 30)
                .frame(maxHeight: .infinity, alignment: .top)
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, 20).padding(.vertical, 18)
        .background(GlassPanel(cornerRadius: 20, tint: .blue))
    }
}

/// Another account: one line of the glass list under the card.
struct AccountRow: View {
    let account: AccountUsage
    let now: Date
    let model: AccountsModel

    var body: some View {
        HStack(spacing: AccountColumns.spacing) {
            VStack(alignment: .leading, spacing: 1) {
                Text(account.label).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                Text(account.email ?? account.name).font(.system(size: 11.5)).foregroundStyle(.secondary)
                    .lineLimit(1)
                StaleNote(account: account, now: now)
            }
            .frame(width: AccountColumns.name, alignment: .leading)
            switch WidgetLayout.kind(of: account, now: now) {
            case .loginLost:
                Label("로그인이 풀렸어요", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .fullWeek(let reset):
                Text("주간 한도 다 씀 · \(Text(UsageText.resetDate(reset)).bold()) 초기화")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .metrics:
                MetricView(window: account.fiveHour, now: now)
                MetricView(window: account.sevenDay, now: now)
            }
            AccountActions(account: account, isActive: false, now: now, model: model)
                .frame(width: AccountColumns.actions, alignment: .trailing)
        }
        .padding(.horizontal, 16).padding(.vertical, 9)
    }
}

/// "3분 전 값 · 조회 실패" when the last refresh could not read this account.
struct StaleNote: View {
    let account: AccountUsage
    let now: Date

    var body: some View {
        if account.status == .error {
            Text(account.fetchedAt.map { "\(UsageText.ago($0, now: now)) 값 · 조회 실패" } ?? "조회 실패")
                .font(.caption2).foregroundStyle(.orange)
        }
    }
}

/// "사용" or "다시 로그인", then the round "⋯" with rename, re-login and remove.
struct AccountActions: View {
    let account: AccountUsage
    let isActive: Bool
    let now: Date
    let model: AccountsModel
    var menuSize: CGFloat = 28

    var body: some View {
        HStack(spacing: 6) {
            switch WidgetLayout.kind(of: account, now: now) {
            case .loginLost:
                Button("다시 로그인") { model.sheet = .relogin(account) }
                    .buttonStyle(PillButtonStyle())
            case .metrics where !isActive:
                Button("사용") { Task { await model.use(account.name) } }
                    .buttonStyle(PillButtonStyle(prominent: true))
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
                CircleGlassIcon(systemName: "ellipsis", size: menuSize)
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
            .accessibilityLabel("\(account.label) 더 보기")
        }
        .disabled(model.isBusy)
    }
}

/// The card's metric: a ring, then label, big percent and reset text.
struct RingMetricView: View {
    let title: String
    let window: UsageWindow?
    let now: Date

    var body: some View {
        HStack(spacing: 14) {
            UsageRing(percent: window?.percent ?? 0, lineWidth: 8).frame(width: 84, height: 84)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                Text(window.map { "\($0.percent)%" } ?? "—")
                    .font(.system(size: 32, weight: .bold, design: .rounded)).monospacedDigit()
                    .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
                Text(UsageText.remaining(until: window?.resetsAt, now: now))
                    .font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A row's metric: bar and percent, the reset text below. A percent whose
/// reset passed is dimmed until the next refresh.
struct MetricView: View {
    let window: UsageWindow?
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                UsageBar(percent: window?.percent ?? 0)
                Text(window.map { "\($0.percent)%" } ?? "—")
                    .font(.system(size: 15, weight: (window?.percent ?? 0) >= 85 ? .heavy : .bold, design: .rounded))
                    .monospacedDigit()
                    .frame(width: 44, alignment: .trailing)
                    .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
            }
            Text(UsageText.remaining(until: window?.resetsAt, now: now))
                .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
