import Foundation

/// A macOS notification's two lines.
public struct AutoSwitchNotice: Equatable, Sendable {
    public var title: String
    public var body: String

    public init(title: String, body: String) {
        self.title = title
        self.body = body
    }
}

/// What auto switch says, in the notifications and the 자동 교체 menu.
public enum AutoSwitchText {
    public static let manualBlocked = "자동 교체가 켜져 있어서 손으로 바꿀 수 없어요"
    /// Beside 사용량's title while auto switch is on.
    public static let onChip = "자동 교체 켜짐"
    /// Shown, not set: the limits are the person's and fixed.
    public static let limits = "5시간 \(AutoSwitchRules.fiveHourLimit)% · 주간 \(AutoSwitchRules.weeklyLimit)%"

    public static func ruleTitle(_ rule: AutoSwitchRule) -> String {
        switch rule {
        case .even: "균등 사용"
        case .weeklyReset: "주간 초기화 가까운 순"
        }
    }

    public static func ruleDetail(_ rule: AutoSwitchRule) -> String {
        switch rule {
        case .even: "주간 사용률이 가장 낮은 계정으로"
        case .weeklyReset: "주간 초기화가 가장 가까운 계정으로"
        }
    }

    /// The menu's one status line (spec 5.7): the first that applies.
    public static func status(settings: AutoSwitchSettings, agent: AgentState, file: UsageFile, live: LiveUsage?,
                              now: Date, timeZone: TimeZone = .current) -> String {
        guard settings.enabled else { return "꺼져 있어요" }
        switch agent {
        case .needsApproval: return "시스템 설정에서 dotsync의 백그라운드 실행을 허용해 주세요"
        case .notInstalled: return "검사 등록이 풀렸어요 — 껐다 다시 켜 주세요"
        case .on, .off: break
        }
        guard live != nil else {
            return "실시간 사용량이 들어오지 않아요 — claude-hud 설정에 display.externalUsageWritePath를 넣어 주세요"
        }
        if file.unsavedSeat != nil { return "저장 안 된 로그인을 쓰는 중이에요 — 저장하면 다시 지켜봐요" }
        if let until = settings.restUntil, until > now {
            let why = settings.restReason.map { "\(restText($0)), " } ?? ""
            return "쉬는 중이에요 — \(why)\(clock(until, timeZone: timeZone))까지"
        }
        return "켜져 있어요 · 지켜보는 중"
    }

    /// One line of the menu's 최근 기록, after its time.
    public static func logLine(_ entry: AutoSwitchLogEntry, labels: [String: String]) -> String {
        func label(_ name: String?) -> String { name.map { labels[$0] ?? $0 } ?? "" }
        switch entry.result {
        case .switched: return "\(label(entry.from)) → \(label(entry.to)) · \(entry.reason ?? "")"
        case .noCandidate: return "바꿀 계정 없음" + (entry.reason.map { " · \($0)" } ?? "")
        case .belowLimit: return "조회해 보니 기준 아래"
        case .unsavedLogin: return "저장 안 된 로그인이라 멈춤"
        case .failed: return "실패: \(entry.message ?? "")"
        }
    }

    private static func restText(_ reason: AutoSwitchRestReason) -> String {
        switch reason {
        case .noCandidate: "바꿀 계정이 없어서"
        case .failed: "교체에 실패해서"
        case .belowLimit: "조회해 보니 기준 아래여서"
        }
    }

    private static func clock(_ date: Date, timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    /// A switch, or the first time no account can take over (spec 5.6).
    /// `previous` is the log entry before `entry`: a full house repeats every
    /// 15 minutes, and one notification is enough. Names show as labels.
    public static func notification(for entry: AutoSwitchLogEntry, previous: AutoSwitchLogEntry?,
                                    labels: [String: String]) -> AutoSwitchNotice? {
        switch entry.result {
        case .switched:
            let from = entry.from.map { labels[$0] ?? $0 } ?? ""
            let to = entry.to.map { labels[$0] ?? $0 } ?? ""
            return AutoSwitchNotice(title: "자동 교체", body: "\(from) → \(to) (\(entry.reason ?? ""))")
        case .noCandidate where previous?.result != .noCandidate:
            return AutoSwitchNotice(title: "자동 교체", body: "바꿀 계정이 없어요 — 모든 계정이 기준을 넘었어요")
        default:
            return nil
        }
    }
}
