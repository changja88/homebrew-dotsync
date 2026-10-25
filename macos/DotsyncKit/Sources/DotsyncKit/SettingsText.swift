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

    /// The 바뀐 내용 column's first line: each changed file with its line
    /// counts, "settings.json +19 −4 · installed_plugins.json".
    public static func changedFiles(_ changes: [SyncChange]) -> String? {
        let parts = changes.filter { $0.kind != "unchanged" }.map { change in
            let (head, _) = splitDetails(change.details)
            return head.isEmpty ? change.label : "\(change.label) \(head)"
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Its second line: the top-level keys that change, "env, hooks".
    public static func changedKeys(_ changes: [SyncChange]) -> String? {
        let keys = changes.filter { $0.kind != "unchanged" }.compactMap { splitDetails($0.details).keys }
        return keys.isEmpty ? nil : keys.joined(separator: ", ")
    }

    /// dotsync writes a file's details as "+19 −4 · env, hooks"; other
    /// details ("2 create, 1 update", "binary · …") stay whole.
    private static func splitDetails(_ details: String) -> (head: String, keys: String?) {
        guard let range = details.range(of: " · ") else { return (details, nil) }
        let head = String(details[..<range.lowerBound])
        guard head.wholeMatch(of: #/\+\d+ −\d+/#) != nil else { return (details, nil) }
        return (head, String(details[range.upperBound...]))
    }

    /// BetterTouchTool's line under its name: its presets, and why they were
    /// not compared.
    public static func bttLine(presets: [String], reason: String?) -> String? {
        var parts: [String] = []
        if !presets.isEmpty { parts.append("프리셋 " + presets.joined(separator: ", ")) }
        if let note = bttNote(reason: reason) { parts.append(note) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The Codex row's line while a Codex delete or cleanup runs; its 받기 ·
    /// 보내기 wait meanwhile, as the CLI's `codex` lock would make them.
    public static func sessionsLine(app: String, codexSessionWork: Bool) -> String? {
        app == "codex" && codexSessionWork ? "세션 정리 중" : nil
    }

    /// The banner's second line: "모두 가져오기" would overwrite these apps'
    /// newer settings on this Mac.
    public static func directionNote(_ report: StatusReport) -> String? {
        let labels = report.apps.filter { isDifferent($0) && $0.direction == "local-newer" }
            .map { appLabel(name: $0.name) }
        guard let last = labels.last else { return nil }
        return "\(labels.joined(separator: ", "))\(topicParticle(after: last)) 이 맥 쪽이 더 최신이에요"
    }

    /// 은 after a closing consonant, 는 after a vowel. Latin names ending in
    /// l, m or n are read with one ("…Tool은").
    static func topicParticle(after word: String) -> String {
        if let last = word.unicodeScalars.last, (0xAC00...0xD7A3).contains(last.value) {
            return (last.value - 0xAC00) % 28 == 0 ? "는" : "은"
        }
        return word.lowercased().last.map { "lmn".contains($0) } == true ? "은" : "는"
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

    /// What to tell the person after a pull or push: the apps that failed
    /// and every warning, or nil when there is nothing to tell.
    public static func applyMessage(_ report: ApplyReport, direction: SyncDirection) -> String? {
        let failed = report.results.filter { $0.outcome == "failed" }
            .map { "\(appLabel(name: $0.app)): \($0.error ?? "알 수 없는 오류")" }
        let warnings = report.results.flatMap { result in
            result.warnings.map { "\(appLabel(name: result.app)): \($0)" }
        }
        if failed.isEmpty && warnings.isEmpty { return nil }
        let header = switch (direction, failed.isEmpty) {
        case (.pull, false): "\(failed.count)개 앱을 가져오지 못했어요"
        case (.push, false): "\(failed.count)개 앱을 내보내지 못했어요"
        case (.pull, true): "가져왔지만 확인할 것이 있어요"
        case (.push, true): "내보냈지만 확인할 것이 있어요"
        }
        return ([header] + failed + warnings).joined(separator: "\n")
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
