import DotsyncKit
import SwiftUI
import WidgetKit

struct UsageEntry: TimelineEntry {
    let date: Date
    let file: UsageFile
}

/// Reads usage.json once and lays out an hour of minutes, so remaining
/// times count down without running anything.
struct UsageProvider: TimelineProvider {
    func placeholder(in context: Context) -> UsageEntry {
        UsageEntry(date: .now, file: .empty)
    }

    func getSnapshot(in context: Context, completion: @escaping (UsageEntry) -> Void) {
        completion(UsageEntry(date: .now, file: load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<UsageEntry>) -> Void) {
        let file = load()
        let entries = WidgetLayout.minuteSchedule(from: .now).map { UsageEntry(date: $0, file: file) }
        completion(Timeline(entries: entries, policy: .atEnd))
    }

    private func load() -> UsageFile {
        UsageStore.shared()?.load() ?? .empty
    }
}

@main
struct DotsyncWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.accounts", provider: UsageProvider()) { entry in
            AccountsWidgetView(entry: entry)
        }
        .configurationDisplayName("Claude 계정")
        .description("Claude Code 계정별 사용량을 보고 계정을 바꿔요.")
        .supportedFamilies([.systemLarge])
    }
}
