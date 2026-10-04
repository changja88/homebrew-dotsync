import AppIntents
import DotsyncKit
import SwiftUI
import WidgetKit

/// The large widget (approved mockup "위젯 A"), laid out for its 344 pt
/// square: content margins are off and the 14 pt padding is ours, so the
/// widths below add up and no reset time gets cut.
struct AccountsWidgetView: View {
    let entry: UsageEntry

    var body: some View {
        let layout = WidgetLayout(file: entry.file, now: entry.date)
        // While a refresh runs, the numbers show as skeleton blocks.
        let loading = entry.file.isRefreshing(at: entry.date)
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Text("Claude 사용량").font(.system(size: 13, weight: .bold))
                // A live time takes all the width it might need, so it is
                // right-aligned in it to sit next to ↻.
                UpdateStatusText(status: UsageText.updateStatus(entry.file, now: entry.date))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                if loading {
                    // 조회 중 already: another press would only wait on this
                    // one, so there is nothing to press.
                    refreshIcon.opacity(0.4)
                } else {
                    Button(intent: RefreshIntent()) { refreshIcon }
                        .buttonStyle(.plain)
                }
            }
            .frame(height: 22)
            InUseCard(layout: layout, now: entry.date, loading: loading)
            VStack(spacing: 5) {
                ForEach(layout.rows) { account in
                    AccountBox(account: account, now: entry.date, confirmFirst: layout.switchNeedsConfirmation,
                               loading: loading)
                }
            }
            if layout.hidden > 0 {
                Link(destination: AppLink.open.url) {
                    HStack(spacing: 3) {
                        Text("외 \(layout.hidden)개 · 앱에서 보기")
                        Image(systemName: "chevron.right").font(.system(size: 7, weight: .bold))
                    }
                    .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    .padding(.leading, 4)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        // Live times are formatted in the environment's language, which is
        // English in the widget whatever the bundle declares; a style's own
        // locale is overridden by it.
        .environment(\.locale, Locale(identifier: "ko_KR"))
        .containerBackground(for: .widget) { WidgetBackdrop() }
        .widgetURL(AppLink.open.url)
    }

    private var refreshIcon: some View {
        Image(systemName: "arrow.clockwise")
            .font(.system(size: 10, weight: .bold))
            .frame(width: 24, height: 24)
            .background(GlassCard(cornerRadius: 12))
    }
}

/// "조회 중…", "갱신 실패", "갱신 전", or how long ago the values were read —
/// a live text that ages by itself between timeline entries.
struct UpdateStatusText: View {
    let status: UsageText.UpdateStatus

    var body: some View {
        switch status {
        case .refreshing: Text("조회 중…")
        case .failed: Text("갱신 실패")
        case .never: Text("갱신 전")
        case .fetched(let at): agoText(at)
        }
    }
}

/// "지금", "3분 전", "2시간 전", "어제" — counts up by itself. A `Text`, not a
/// view, so it can sit inside another text.
func agoText(_ date: Date) -> Text {
    Text(.currentDate, format: SystemFormatStyle.DateReference(
        to: date, allowedFields: [.day, .hour, .minute], maxFieldCount: 2, thresholdField: .day))
}

/// "3시간 44분", "6일 20시간" until a reset, counting down by itself;
/// "초기화됨" from the timeline entry at the reset on.
struct CountdownText: View {
    let reset: Date?
    let now: Date
    /// Adds " 후": "3시간 44분 후".
    var after = false

    var body: some View {
        switch UsageText.countdown(to: reset, now: now) {
        case .unknown: Text("—")
        case .passed: Text("초기화됨")
        case .until(let date):
            let left = Text(.currentDate, format: SystemFormatStyle.DateOffset(
                to: date, allowedFields: [.day, .hour, .minute], maxFieldCount: 2, sign: .never))
            if after { Text("\(left) 후") } else { left }
        }
    }
}

struct InUseCard: View {
    let layout: WidgetLayout
    let now: Date
    let loading: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("지금 사용 중").font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer(minLength: 8)
                HStack(spacing: 5) {
                    if layout.current != nil || layout.unsavedSeat != nil {
                        Circle().fill(.blue).frame(width: 7, height: 7)
                    }
                    Text(title).font(.system(size: 13, weight: .bold))
                        .lineLimit(1).minimumScaleFactor(0.75).truncationMode(.middle)
                }
            }
            if layout.current?.status == .loginRequired {
                HStack {
                    Label("로그인이 풀렸어요", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11, weight: .semibold)).foregroundStyle(.orange)
                    Spacer()
                    if let name = layout.current?.name {
                        Link(destination: AppLink.relogin(name).url) { PillLabel(text: "앱 열기", prominent: false) }
                    }
                }
            } else {
                HStack(spacing: 12) {
                    RingMetric(title: "5시간", window: five, now: now)
                    RingMetric(title: "주간", window: week, now: now)
                }
                .redacted(reason: loading ? .placeholder : [])
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(GlassCard(cornerRadius: 16, tint: .blue))
    }

    private var title: String {
        if let current = layout.current { return current.label }
        if let seat = layout.unsavedSeat { return "\(seat.email ?? "알 수 없는 계정") · 저장 안 됨" }
        return "로그인 안 됨"
    }

    private var five: UsageWindow? { layout.current?.fiveHour ?? layout.unsavedSeat?.fiveHour }
    private var week: UsageWindow? { layout.current?.sevenDay ?? layout.unsavedSeat?.sevenDay }
}

/// The in-use card's metric: ring, then label, big percent and
/// "3시간 44분 후" stacked beside it. 88 pt of text room fits "6일 23시간 후".
struct RingMetric: View {
    let title: String
    let window: UsageWindow?
    let now: Date

    var body: some View {
        HStack(spacing: 8) {
            UsageRing(percent: window?.percent ?? 0, lineWidth: 5).frame(width: 44, height: 44)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 10.5)).foregroundStyle(.secondary)
                Text(window.map { "\($0.percent)%" } ?? "—")
                    .font(.system(size: 21, weight: .bold, design: .rounded)).monospacedDigit()
                    .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
                CountdownText(reset: window?.resetsAt, now: now, after: true)
                    .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.85)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One other account: a 38 pt glass row — name, two metrics, "사용".
struct AccountBox: View {
    let account: AccountUsage
    let now: Date
    let confirmFirst: Bool
    let loading: Bool

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(account.label).font(.system(size: 12, weight: .semibold))
                    .lineLimit(1).minimumScaleFactor(0.75)
                if account.status == .error, let at = account.fetchedAt {
                    Text("\(agoText(at)) 값").font(.system(size: 8.5)).foregroundStyle(.secondary)
                        .lineLimit(1).minimumScaleFactor(0.8)
                }
            }
            .frame(width: 64, alignment: .leading)
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
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.8)
                    .redacted(reason: loading ? .placeholder : [])
                Spacer(minLength: 0)
            case .loginLost:
                Label("로그인이 풀렸어요", systemImage: "exclamationmark.triangle")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.orange)
                Spacer(minLength: 0)
                Link(destination: AppLink.relogin(account.name).url) { PillLabel(text: "앱 열기", prominent: false) }
            }
        }
        .padding(.leading, 10).padding(.trailing, 8)
        .frame(height: 38)
        .background(GlassCard(cornerRadius: 12))
    }

    @ViewBuilder private var useButton: some View {
        if confirmFirst {
            Link(destination: AppLink.use(account.name).url) { PillLabel(text: "사용", prominent: true) }
        } else {
            Button(intent: UseAccountIntent(name: account.name)) { PillLabel(text: "사용", prominent: true) }
                .buttonStyle(.plain)
        }
    }
}

/// A row's metric: bar over the time left, and the percent beside both,
/// centered on them. The column is about 84 pt: "100%" takes 30, leaving
/// 49 for "6일 23시간", which needs 48.
struct RowMetric: View {
    let window: UsageWindow?
    let now: Date

    var body: some View {
        HStack(alignment: .center, spacing: 5) {
            VStack(alignment: .leading, spacing: 3) {
                UsageBar(percent: window?.percent ?? 0, height: 4)
                CountdownText(reset: window?.resetsAt, now: now)
                    .font(.system(size: 9.5)).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.85)
            }
            Text(window.map { "\($0.percent)%" } ?? "—")
                .font(.system(size: 12.5, weight: (window?.percent ?? 0) >= 85 ? .heavy : .bold, design: .rounded))
                .monospacedDigit().fixedSize()
                .opacity(UsageText.isReset(window, now: now) ? 0.4 : 1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "사용" and "앱 열기". Clear, tinted and the dimmed desktop draw everything
/// in one color, so the blue fill turns into a plain one there — white text
/// on white would vanish.
struct PillLabel: View {
    let text: String
    let prominent: Bool
    @Environment(\.widgetRenderingMode) private var renderingMode
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let blue = prominent && renderingMode == .fullColor
        Text(text).font(.system(size: 11, weight: .semibold))
            .foregroundStyle(blue ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
            .padding(.horizontal, 10).frame(height: 22)
            .background {
                if blue {
                    Capsule().fill(LinearGradient(colors: [Color(red: 0.29, green: 0.65, blue: 1.0),
                                                           Color(red: 0.04, green: 0.45, blue: 0.95)],
                                                  startPoint: .top, endPoint: .bottom))
                        .overlay(Capsule().strokeBorder(.white.opacity(0.35), lineWidth: 0.5))
                } else {
                    GlassCard(cornerRadius: 11)
                }
            }
            .fixedSize()
    }
}

/// Reads as glass without live refraction, which widgets can't do on the
/// Mac: a light gradient, a bright top edge and a soft shadow.
struct GlassCard: View {
    let cornerRadius: CGFloat
    var tint: Color? = nil
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        let dark = colorScheme == .dark
        shape
            .fill(LinearGradient(colors: fill(dark: dark), startPoint: .top, endPoint: .bottom))
            .overlay(shape.strokeBorder(
                LinearGradient(colors: [.white.opacity(dark ? 0.28 : 0.95), .white.opacity(dark ? 0.04 : 0.3)],
                               startPoint: .top, endPoint: .bottom),
                lineWidth: 1))
            .shadow(color: .black.opacity(dark ? 0.3 : 0.08), radius: 2, y: 1)
    }

    private func fill(dark: Bool) -> [Color] {
        if let tint { return [tint.opacity(dark ? 0.36 : 0.3), tint.opacity(dark ? 0.16 : 0.12)] }
        return dark ? [.white.opacity(0.13), .white.opacity(0.05)] : [.white.opacity(0.72), .white.opacity(0.4)]
    }
}

/// A soft tinted ground, so the glass cards have something to sit on. The
/// dimmed desktop replaces it with the system's own.
struct WidgetBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let colors = colorScheme == .dark
            ? [Color(red: 0.11, green: 0.14, blue: 0.24), Color(red: 0.14, green: 0.11, blue: 0.20),
               Color(red: 0.16, green: 0.12, blue: 0.12)]
            : [Color(red: 0.88, green: 0.92, blue: 0.99), Color(red: 0.92, green: 0.90, blue: 0.97),
               Color(red: 0.97, green: 0.92, blue: 0.90)]
        LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
}
