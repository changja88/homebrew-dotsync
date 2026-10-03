import SwiftUI
import WidgetKit

struct PlaceholderEntry: TimelineEntry {
    let date: Date
}

struct PlaceholderProvider: TimelineProvider {
    func placeholder(in context: Context) -> PlaceholderEntry { PlaceholderEntry(date: .now) }
    func getSnapshot(in context: Context, completion: @escaping (PlaceholderEntry) -> Void) {
        completion(PlaceholderEntry(date: .now))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<PlaceholderEntry>) -> Void) {
        completion(Timeline(entries: [PlaceholderEntry(date: .now)], policy: .never))
    }
}

@main
struct DotsyncWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.accounts", provider: PlaceholderProvider()) { _ in
            Text("dotsync").containerBackground(.background, for: .widget)
        }
        .supportedFamilies([.systemLarge])
    }
}
