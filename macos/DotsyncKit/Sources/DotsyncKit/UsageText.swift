import Foundation

public enum UsageTier: Sendable {
    case low, mid, high
}

/// The Korean texts the widget and the window show.
public enum UsageText {
    /// "3시간 9분 후 초기화", "4일 23시간 후 초기화", "12분 후 초기화",
    /// "초기화됨" once the time passed, "—" without a reset time.
    public static func remaining(until reset: Date?, now: Date) -> String {
        guard let reset else { return "—" }
        guard let span = span(until: reset, now: now) else { return "초기화됨" }
        return "\(span) 후 초기화"
    }

    /// The short form for a list row: "1시간 20분", "1일 8시간".
    public static func remainingShort(until reset: Date?, now: Date) -> String {
        guard let reset else { return "—" }
        return span(until: reset, now: now) ?? "초기화됨"
    }

    /// "주간 한도 다 씀 · 10/7(수) 09:00 초기화"
    public static func fullWeek(reset: Date, timeZone: TimeZone = .current) -> String {
        "주간 한도 다 씀 · \(resetDate(reset, timeZone: timeZone)) 초기화"
    }

    /// "10/7(수) 09:00". Rounds to the nearest minute: Claude reports resets
    /// like 08:59:59.85, which people read as 09:00.
    public static func resetDate(_ date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ko_KR")
        formatter.timeZone = timeZone
        formatter.dateFormat = "M/d(E) HH:mm"
        let minute = (date.timeIntervalSince1970 / 60).rounded() * 60
        return formatter.string(from: Date(timeIntervalSince1970: minute))
    }

    /// "방금", "3분 전", "2시간 전", "1일 전"; "갱신 전" before the first refresh.
    public static func ago(_ date: Date?, now: Date) -> String {
        guard let date else { return "갱신 전" }
        let seconds = max(0, now.timeIntervalSince(date))
        if seconds < 60 { return "방금" }
        if seconds < 3600 { return "\(Int(seconds / 60))분 전" }
        if seconds < 86_400 { return "\(Int(seconds / 3600))시간 전" }
        return "\(Int(seconds / 86_400))일 전"
    }

    /// The widget header: "조회 중…" while a refresh runs, "갱신 실패" after a
    /// failed one, otherwise `ago`.
    public static func updated(_ file: UsageFile, now: Date) -> String {
        if file.isRefreshing(at: now) { return "조회 중…" }
        if file.lastError != nil { return "갱신 실패" }
        return ago(file.fetchedAt, now: now)
    }

    /// Weekly use at 98 % or more, until its reset passes: the row shows only
    /// when it resets.
    public static func isFull(_ week: UsageWindow?, now: Date) -> Bool {
        guard let week, week.percent >= 98 else { return false }
        guard let reset = week.resetsAt else { return true }
        return reset > now
    }

    /// The window's reset time passed, so its percent is out of date.
    public static func isReset(_ window: UsageWindow?, now: Date) -> Bool {
        guard let reset = window?.resetsAt else { return false }
        return reset <= now
    }

    public static func tier(_ percent: Int) -> UsageTier {
        if percent >= 85 { return .high }
        if percent >= 60 { return .mid }
        return .low
    }

    /// "4일 23시간", "3시간 9분", "3시간", "12분"; nil once `reset` passed.
    /// Minutes round up, so a reset 30 s away still reads "1분".
    static func span(until reset: Date, now: Date) -> String? {
        let seconds = reset.timeIntervalSince(now)
        guard seconds > 0 else { return nil }
        let minutes = Int((seconds / 60).rounded(.up))
        let days = minutes / 1440
        let hours = (minutes % 1440) / 60
        let rest = minutes % 60
        if days > 0 { return hours > 0 ? "\(days)일 \(hours)시간" : "\(days)일" }
        if hours > 0 { return rest > 0 ? "\(hours)시간 \(rest)분" : "\(hours)시간" }
        return "\(rest)분"
    }
}
