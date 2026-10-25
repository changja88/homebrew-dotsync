import Foundation

/// The settings screen's words, as pure functions of dotsync's answers.
public enum SettingsText {
    /// The 상태 column.
    public static func stateLabel(_ app: AppSyncStatus) -> String {
        switch app.state {
        case "clean": return "같음"
        case "dirty" where app.direction == "local-newer": return "이 맥이 최신"
        case "dirty" where app.direction == "folder-newer": return "폴더가 최신"
        case "dirty", "missing": return "다름"
        case "unknown": return "확인 못 함"
        default: return "—"
        }
    }

    /// A tracked app whose folder copy differs from this Mac.
    public static func isDifferent(_ app: AppSyncStatus) -> Bool {
        app.tracked && (app.state == "dirty" || app.state == "missing")
    }

    public static func badgeCount(_ report: StatusReport) -> Int {
        report.apps.filter(isDifferent).count
    }

    public static func banner(_ report: StatusReport) -> String? {
        let count = badgeCount(report)
        return count == 0 ? nil : "\(count)개 앱이 폴더와 달라요"
    }

    /// Why BetterTouchTool's preset could not be compared, when the person
    /// can do something about it.
    public static func bttNote(reason: String?) -> String? {
        switch reason {
        case "btt_permission": return "자동화 권한이 필요해요"
        case "btt_not_running": return "BTT가 켜져 있어야 비교해요"
        case "timeout": return "BTT가 제때 답하지 않았어요"
        default: return nil
        }
    }

    public static func pluginActionsText(_ actions: PluginActions) -> String? {
        var parts: [String] = []
        if !actions.install.isEmpty { parts.append("\(actions.install.count)개 설치") }
        if !actions.disable.isEmpty { parts.append("\(actions.disable.count)개 끄기") }
        return parts.isEmpty ? nil : "플러그인 " + parts.joined(separator: " · ")
    }

    /// "방금 확인", "3분 전 확인".
    public static func checkedAgo(_ date: Date, now: Date) -> String {
        UsageText.ago(date, now: now) + " 확인"
    }

    public static func pullTitle(appLabel: String) -> String {
        "\(appLabel) 설정을 이 맥으로 가져올까요?"
    }

    public static func pushTitle(appLabel: String) -> String {
        "\(appLabel) 설정을 폴더로 내보낼까요?"
    }

    public static func appLabel(name: String) -> String {
        switch name {
        case "claude": return "Claude Code"
        case "codex": return "Codex"
        case "bettertouchtool": return "BetterTouchTool"
        case "ghostty": return "Ghostty"
        case "skills": return "스킬"
        default: return name
        }
    }
}
