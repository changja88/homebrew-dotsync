import DotsyncKit
import SwiftUI
import WidgetKit

struct SystemStatsEntry: TimelineEntry {
    let date: Date
    let stats: SystemStats
}

/// Reads the machine when WidgetKit asks; nothing runs between reloads.
struct SystemStatsProvider: TimelineProvider {
    func placeholder(in context: Context) -> SystemStatsEntry {
        SystemStatsEntry(date: .now, stats: .unknown)
    }

    func getSnapshot(in context: Context, completion: @escaping (SystemStatsEntry) -> Void) {
        Task { completion(await read()) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SystemStatsEntry>) -> Void) {
        Task {
            let entry = await read()
            completion(Timeline(entries: [entry], policy: .after(entry.date.addingTimeInterval(WidgetLayout.liveReload))))
        }
    }

    private func read() async -> SystemStatsEntry {
        let stats = await SystemReader.read()
        return SystemStatsEntry(date: .now, stats: stats)
    }
}

/// CPU, GPU and RAM. Not about Claude, so it needs neither the app nor dotsync.
struct SystemStatsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "dotsync.system", provider: SystemStatsProvider()) { entry in
            SystemStatsWidgetView(entry: entry)
        }
        .configurationDisplayName("맥 상태")
        .description("CPU, GPU, RAM 사용률을 보여줘요.")
        .supportedFamilies([.systemSmall, .systemMedium])
        .contentMarginsDisabled()
    }
}

struct SystemStatsWidgetView: View {
    let entry: SystemStatsEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        SystemStatsContent(entry: entry, small: family == .systemSmall).modifier(WidgetChrome())
    }
}

/// The approved mockups: three rings on medium, three lines on small.
struct SystemStatsContent: View {
    let entry: SystemStatsEntry
    let small: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Text("맥 상태").font(.system(size: 13, weight: .bold)).lineLimit(1).fixedSize()
                agoText(entry.date)
                    .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                Button(intent: ReadSystemStatsIntent()) { RefreshIcon() }
                    .buttonStyle(.plain)
            }
            .frame(height: 22)
            // From a press of ↻ until the widget is drawn again, the system
            // shows the numbers as loading.
            Group {
                if small {
                    VStack(spacing: 5) {
                        SystemLine(name: "CPU", percent: entry.stats.cpu)
                        SystemLine(name: "GPU", percent: entry.stats.gpu)
                        SystemLine(name: "RAM", percent: entry.stats.ram)
                    }
                } else {
                    HStack(spacing: 4) {
                        SystemRing(name: "CPU", percent: entry.stats.cpu)
                        SystemRing(name: "GPU", percent: entry.stats.gpu)
                        SystemRing(name: "RAM", percent: entry.stats.ram)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(GlassPanel(cornerRadius: 16, tint: .blue, tintStrength: 0.06, shadowRadius: 2))
                }
            }
            .invalidatableContent()
        }
        .padding(14)
    }
}

/// "34%", or "—" for a value that could not be read.
private func percentText(_ percent: Int?) -> String {
    percent.map { "\($0)%" } ?? "—"
}

private struct SystemRing: View {
    let name: String
    let percent: Int?

    var body: some View {
        VStack(spacing: 4) {
            ZStack {
                UsageRing(percent: percent ?? 0, lineWidth: 6)
                Text(percentText(percent))
                    .font(.system(size: 15, weight: .bold, design: .rounded)).monospacedDigit()
                    .lineLimit(1).minimumScaleFactor(0.7)
                    .frame(width: 38)  // "100%" stays inside the 44 pt the ring leaves
            }
            .frame(width: 56, height: 56)
            Text(name).font(.system(size: 10.5)).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct SystemLine: View {
    let name: String
    let percent: Int?

    var body: some View {
        HStack(spacing: 6) {
            Text(name).font(.system(size: 11, weight: .semibold))
                .frame(width: 26, alignment: .leading)
            UsageBar(percent: percent ?? 0, height: 6)
            Text(percentText(percent))
                .font(.system(size: 13, weight: .bold, design: .rounded)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.8)
                .frame(width: 32, alignment: .trailing)
        }
        .padding(.horizontal, 8)
        .frame(maxHeight: .infinity)
        .background(GlassPanel(cornerRadius: 10, shadowRadius: 2))
    }
}
