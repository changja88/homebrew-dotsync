import Foundation

/// The decisions behind the delete sheets: what the confirm sheet counts, how
/// a result reads, and whether a result left by a closed app should show.
/// Pure logic: the app only draws what comes back.
public enum SessionDeleteRules {
    /// What the confirm sheet says about the sessions it is about to delete.
    public struct ConfirmCounts: Equatable, Sendable {
        public var count: Int
        /// Codex: the threads spawned below the picked ones, deleted with them.
        public var children: Int
        public var bytes: Int64
    }

    /// Counts the picked sessions that are still in `list` and idle, like
    /// `SessionRules.selectionTotals`, plus their spawned threads.
    public static func confirmCounts(_ ids: Set<String>, in list: SessionList) -> ConfirmCounts {
        let picked = list.groups.flatMap(\.items).filter { ids.contains($0.id) && SessionRules.isSelectable($0) }
        return ConfirmCounts(
            count: picked.count, children: picked.reduce(0) { $0 + ($1.children ?? 0) },
            bytes: picked.reduce(0) { $0 + $1.bytes })
    }

    /// What the CLI says it freed, summed; a result with no size counts 0.
    public static func freedBytes(_ report: DeleteReport) -> Int64 {
        report.results.reduce(0) { $0 + ($1.freedBytes ?? 0) }
    }

    /// The row's name: the title in the current list, or, when the list has
    /// none (not loaded, or the session is gone), the id's first 8 characters.
    public static func rowTitle(_ id: String, in list: SessionList?) -> String {
        if let list, let item = SessionRules.item(id, in: list) { return item.title }
        return String(id.prefix(8))
    }

    /// Only a partial delete can be tried again; a skipped one was left whole
    /// on purpose.
    public static func canRetry(_ result: DeleteResult) -> Bool {
        result.outcome == "partial"
    }

    /// `retry`'s results take the place of the ones for the same ids; the rest
    /// stay as they were, in their order.
    public static func merged(_ report: DeleteReport, replacing retry: DeleteReport) -> DeleteReport {
        let fresh = Dictionary(retry.results.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
        var result = report
        result.results = report.results.map { fresh[$0.id] ?? $0 }
        result.stopped = report.stopped || retry.stopped
        return result
    }

    // MARK: A result the app did not see

    /// The start time kept in `pendingSessionsResult`, to whole seconds and
    /// rounded down so it is never later than the log's own start time.
    public static func pendingStamp(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }

    public static func pendingStart(_ stamp: String?) -> Date? {
        stamp.flatMap { ISO8601DateFormatter().date(from: $0) }
    }

    /// What to show after a relaunch: the report a delete or recover logged.
    /// Nil when there is no log, the run left no report (refused or busy), or
    /// it was a cleanup, which has its own sheet.
    public static func pendingResult(_ log: SessionLogEntry?) -> (tool: SessionTool, report: DeleteReport)? {
        guard let log, case .delete(let report)? = log.result else { return nil }
        return (report.tool, report)
    }
}
