import AppIntents
import DotsyncKit
import SwiftUI
import WidgetKit

struct AccountsWidgetView: View {
    let entry: UsageEntry

    var body: some View {
        let layout = WidgetLayout(file: entry.file, now: entry.date)
        // While a refresh runs, the numbers show as skeleton blocks.
        let loading = entry.file.isRefreshing(at: entry.date)
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Claude 사용량").font(.system(size: 12.5, weight: .semibold))
                Spacer()
                Text(UsageText.updated(entry.file, now: entry.date))
                    .font(.system(size: 10.5)).foregroundStyle(.secondary)
                Button(intent: RefreshIntent()) {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10, weight: .semibold))
                }
                .buttonStyle(.bordered).controlSize(.mini)
            }
            InUseCard(layout: layout, now: entry.date, loading: loading)
            ForEach(layout.rows) { account in
                AccountBox(account: account, now: entry.date, confirmFirst: layout.switchNeedsConfirmation,
                           loading: loading)
            }
            if layout.hidden > 0 {
                Link(destination: AppLink.open.url) {
                    Text("외 \(layout.hidden)개 · 앱에서 보기").font(.system(size: 10.5)).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .containerBackground(.background, for: .widget)
        .widgetURL(AppLink.open.url)
    }
}

struct InUseCard: View {
    let layout: WidgetLayout
    let now: Date
    let loading: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("지금 사용 중").font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer()
                Text(title).font(.system(size: 12.5, weight: .semibold)).lineLimit(1)
            }
            if layout.current?.status == .loginRequired {
                HStack {
                    Text("⚠ 로그인이 풀렸어요").font(.system(size: 11)).foregroundStyle(.orange)
                    Spacer()
                    if let name = layout.current?.name {
                        Link("앱 열기", destination: AppLink.relogin(name).url).font(.system(size: 10))
                    }
                }
            } else {
                HStack(alignment: .top, spacing: 14) {
                    BigMetric(title: "5시간", window: five, now: now)
                    BigMetric(title: "주간", window: week, now: now)
                }
                .redacted(reason: loading ? .placeholder : [])
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 12))
    }

    private var title: String {
        if let current = layout.current { return "● \(current.label)" }
        if let seat = layout.unsavedSeat { return "\(seat.email ?? "알 수 없는 계정") · 저장 안 됨" }
        return "로그인 안 됨"
    }

    private var five: UsageWindow? { layout.current?.fiveHour ?? layout.unsavedSeat?.fiveHour }
    private var week: UsageWindow? { layout.current?.sevenDay ?? layout.unsavedSeat?.sevenDay }
}

/// The in-use card's metric: label, bar, big percent spanning two rows,
/// "… 후 초기화" with room under the bar.
struct BigMetric: View {
    let title: String
    let window: UsageWindow?
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 10.5)).foregroundStyle(.secondary)
            HStack(spacing: 6) {
                VStack(alignment: .leading, spacing: 5) {
                    UsageBar(percent: window?.percent ?? 0, height: 7)
                    Text(UsageText.remaining(until: window?.resetsAt, now: now))
                        .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
                Text(window.map { "\($0.percent)%" } ?? "—")
                    .font(.system(size: 24, weight: .semibold)).monospacedDigit()
                    .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One other account: its own rounded box, 12 pt top and bottom.
struct AccountBox: View {
    let account: AccountUsage
    let now: Date
    let confirmFirst: Bool
    let loading: Bool
    /// Clear, tinted and the dimmed desktop draw everything in one color, so
    /// a filled blue button would turn into white text on white.
    @Environment(\.widgetRenderingMode) private var renderingMode
    private var fullColor: Bool { renderingMode == .fullColor }

    var body: some View {
        HStack(spacing: 9) {
            VStack(alignment: .leading, spacing: 1) {
                Text(account.label).font(.system(size: 11.5)).lineLimit(1)
                if account.status == .error, let at = account.fetchedAt {
                    Text("\(UsageText.ago(at, now: now)) 값").font(.system(size: 8.5)).foregroundStyle(.secondary)
                }
            }
            .frame(width: 54, alignment: .leading)
            switch WidgetLayout.kind(of: account, now: now) {
            case .metrics:
                Group {
                    RowMetric(window: account.fiveHour, now: now)
                    RowMetric(window: account.sevenDay, now: now)
                }
                .redacted(reason: loading ? .placeholder : [])
                useButton
            case .fullWeek(let reset):
                Text("주간 한도 다 씀 · \(Text(UsageText.resetDate(reset)).bold()) 초기화")
                    .font(.system(size: 11.5)).foregroundStyle(.secondary)
                    .redacted(reason: loading ? .placeholder : [])
                Spacer(minLength: 0)
            case .loginLost:
                Text("⚠ 로그인이 풀렸어요").font(.system(size: 10)).foregroundStyle(.orange)
                Spacer(minLength: 0)
                Link("앱 열기", destination: AppLink.relogin(account.name).url).font(.system(size: 9.5))
            }
        }
        .padding(.horizontal, 11).padding(.vertical, 12)
        .background(.fill.quaternary, in: RoundedRectangle(cornerRadius: 9))
    }

    @ViewBuilder private var useButton: some View {
        if confirmFirst {
            Link(destination: AppLink.use(account.name).url) {
                Text("사용").font(.system(size: 10))
                    .foregroundStyle(fullColor ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Capsule().fill(fullColor ? AnyShapeStyle(.blue) : AnyShapeStyle(.fill.secondary)))
            }
        } else if fullColor {
            Button(intent: UseAccountIntent(name: account.name)) {
                Text("사용").font(.system(size: 10))
            }
            .buttonStyle(.borderedProminent).controlSize(.mini)
        } else {
            Button(intent: UseAccountIntent(name: account.name)) {
                Text("사용").font(.system(size: 10))
            }
            .buttonStyle(.bordered).controlSize(.mini)
        }
    }
}

/// A list row's metric: bar, percent spanning two rows, short reset text.
struct RowMetric: View {
    let window: UsageWindow?
    let now: Date

    var body: some View {
        HStack(spacing: 5) {
            VStack(alignment: .leading, spacing: 4) {
                UsageBar(percent: window?.percent ?? 0, height: 5)
                Text(UsageText.remainingShort(until: window?.resetsAt, now: now))
                    .font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
            }
            Text(window.map { "\($0.percent)%" } ?? "—")
                .font(.system(size: 15, weight: (window?.percent ?? 0) >= 85 ? .heavy : .semibold))
                .monospacedDigit()
                .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
