import DotsyncKit
import SwiftUI
import WidgetKit

struct UsageEntry: TimelineEntry {
    let date: Date
    let file: UsageFile
    /// Claude's status line as read with `file`: the in-use widget's header
    /// says when it reported, and its ↻ only reads it again.
    var live: LiveUsage?
    /// No "사용" buttons while it is on (spec 5.8).
    var autoSwitchOn = false
}

/// Reads usage.json once and adds an entry only where the widget changes;
/// remaining times are live texts that count down by themselves.
struct UsageProvider: TimelineProvider {
    /// The widget shows the account in use, whose numbers Claude's status
    /// line moves on between refreshes.
    let showsInUse: Bool

    func placeholder(in context: Context) -> UsageEntry {
        UsageEntry(date: .now, file: .empty)
    }

    func getSnapshot(in context: Context, completion: @escaping (UsageEntry) -> Void) {
        let (file, live) = load()
        completion(UsageEntry(date: .now, file: file, live: live, autoSwitchOn: autoSwitchOn()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<UsageEntry>) -> Void) {
        let (file, live) = load()
        let autoSwitchOn = autoSwitchOn()
        let now = Date.now
        let entries = WidgetLayout.timelineDates(for: file, now: now).map {
            UsageEntry(date: $0, file: file, live: live, autoSwitchOn: autoSwitchOn)
        }
        completion(Timeline(entries: entries,
                            policy: .after(WidgetLayout.nextReload(after: now, showsInUse: showsInUse))))
    }

    /// auto-switch.json beside usage.json; the app redraws the widget when it
    /// turns auto switch on or off.
    private func autoSwitchOn() -> Bool {
        AutoSwitchStore.shared()?.loadSettings().enabled ?? false
    }

    /// usage.json, with the active account's numbers from Claude's status line
    /// when those are newer, and that status line. Read only when WidgetKit
    /// asks for a timeline, not each time the widget comes on screen.
    private func load() -> (file: UsageFile, live: LiveUsage?) {
        guard let store = UsageStore.shared() else { return (.empty, nil) }
        let live = store.loadLive()
        return (UsageMerge.withLive(store.load() ?? .empty, live), live)
    }
}

@main
struct DotsyncWidgets: WidgetBundle {
    var body: some Widget {
        AccountsWidget()
        InUseWidget()
        AccountListWidget()
    }
}

/// The in-use card and the list together.
struct AccountsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.accounts", provider: UsageProvider(showsInUse: true)) { entry in
            AccountsWidgetView(entry: entry)
        }
        .configurationDisplayName("Claude 계정")
        .description("Claude Code 계정별 사용량을 보고 계정을 바꿔요.")
        .supportedFamilies([.systemLarge, .systemExtraLarge])
        .contentMarginsDisabled()
    }
}

/// The in-use card alone — over "Claude 계정 목록" in Notification Center,
/// the two read as one tall widget.
struct InUseWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.inuse", provider: UsageProvider(showsInUse: true)) { entry in
            InUseWidgetView(entry: entry)
        }
        .configurationDisplayName("지금 사용 중")
        .description("Claude Code가 지금 쓰는 계정의 사용량을 보여줘요.")
        .supportedFamilies([.systemMedium])
        .contentMarginsDisabled()
    }
}

/// The other accounts alone, up to seven.
struct AccountListWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.list", provider: UsageProvider(showsInUse: false)) { entry in
            AccountListWidgetView(entry: entry)
        }
        .configurationDisplayName("Claude 계정 목록")
        .description("다른 계정들의 사용량을 보고 계정을 바꿔요.")
        .supportedFamilies([.systemLarge])
        .contentMarginsDisabled()
    }
}
