import Foundation

/// Which sessions can be picked, quick-select by age, and how groups become
/// rows on the session screen. Pure logic: the screen only draws the result.
public enum SessionRules {
    /// The groups that never merge into a "외 N개 프로젝트" row.
    private static let fixedKeys: Set<String> = ["outside", "unknown"]

    /// Only an idle session can be picked: running, unknown, background,
    /// desktop and interrupted ones cannot.
    public static func isSelectable(_ item: SessionItem) -> Bool {
        item.state == "idle"
    }

    public static func selectable(in group: SessionGroup) -> Set<String> {
        Set(group.items.filter(isSelectable).map(\.id))
    }

    /// The idle sessions whose last record is more than `days` days old.
    public static func quickSelect(_ list: SessionList, olderThanDays days: Int, now: Date) -> Set<String> {
        let cutoff = now.addingTimeInterval(-Double(days) * 86_400)
        let old = list.groups.flatMap(\.items).filter { isSelectable($0) && $0.lastActivity < cutoff }
        return Set(old.map(\.id))
    }

    /// What `ids` adds up to. Ids that are not in the list, or are no longer
    /// idle after a refresh, do not count.
    public static func selectionTotals(_ ids: Set<String>, in list: SessionList) -> (count: Int, bytes: Int64) {
        let picked = list.groups.flatMap(\.items).filter { ids.contains($0.id) && isSelectable($0) }
        return (picked.count, picked.reduce(0) { $0 + $1.bytes })
    }

    /// What a group shows before "나머지 N개 보기".
    private static let collapsedCount = 5

    /// The first rows of a group, or all of them with `showAll`.
    public static func visibleItems(_ group: SessionGroup, showAll: Bool) -> [SessionItem] {
        showAll ? group.items : Array(group.items.prefix(collapsedCount))
    }

    /// The same for a row, which may be the merged one.
    public static func visibleItems(_ row: DisplayRow, showAll: Bool) -> [SessionItem] {
        showAll ? row.items : Array(row.items.prefix(collapsedCount))
    }

    public static func selectable(in row: DisplayRow) -> Set<String> {
        Set(row.items.filter(isSelectable).map(\.id))
    }

    /// The part of `selection` that is still in the list and still idle:
    /// what is left to pick after a refresh.
    public static func pruned(_ selection: Set<String>, in list: SessionList) -> Set<String> {
        selection.intersection(list.groups.flatMap(\.items).filter(isSelectable).map(\.id))
    }

    public enum CheckState: Equatable { case none, some, all }

    /// A group checkbox over the ids it can pick; with nothing to pick it
    /// stays unchecked.
    public static func checkState(_ selection: Set<String>, among ids: Set<String>) -> CheckState {
        let picked = selection.intersection(ids).count
        if picked == 0 { return .none }
        return picked == ids.count ? .all : .some
    }

    /// A group checkbox: picks every idle session of the group, or, when all
    /// are picked already, lets them go. Other picks stay.
    public static func togglingGroup(_ selection: Set<String>, selectable ids: Set<String>) -> Set<String> {
        checkState(selection, among: ids) == .all ? selection.subtracting(ids) : selection.union(ids)
    }

    public enum DisplayRow: Equatable, Identifiable {
        case group(SessionGroup)
        case merged(title: String, subtitle: String, members: [SessionGroup])

        public var id: String {
            switch self {
            case .group(let group): return group.id
            // One merged row at most; a fixed id keeps UI state across refreshes.
            case .merged: return "merged"
            }
        }

        private var groups: [SessionGroup] {
            switch self {
            case .group(let group): return [group]
            case .merged(_, _, let members): return members
            }
        }

        /// A merged row lists its members' sessions, newest first; a plain
        /// group keeps the CLI's order.
        public var items: [SessionItem] {
            switch self {
            case .group(let group): return group.items
            case .merged(_, _, let members):
                return members.flatMap(\.items).enumerated()
                    .sorted { ($0.element.lastActivity, $1.offset) > ($1.element.lastActivity, $0.offset) }
                    .map(\.element)
            }
        }

        public var bytes: Int64 { groups.reduce(0) { $0 + $1.bytes } }
        public var sessions: Int { groups.reduce(0) { $0 + $1.sessions } }
        public var running: Int { groups.reduce(0) { $0 + $1.running } }
    }

    /// At most `limit` rows, biggest first. Past the limit the smallest repo
    /// groups become one row; `outside` and `unknown` stay as they are.
    public static func rows(_ groups: [SessionGroup], limit: Int = 6) -> [DisplayRow] {
        let repos = groups.filter { !fixedKeys.contains($0.key) }
        let fixed = groups.filter { fixedKeys.contains($0.key) }
        // Merging k groups into one row removes k - 1 rows.
        let mergeCount = min(max(groups.count - limit + 1, 0), repos.count)
        var rows: [DisplayRow] = fixed.map(DisplayRow.group)
        if mergeCount >= 2 {
            let bySize = repos.enumerated().sorted { ($0.element.bytes, $1.offset) > ($1.element.bytes, $0.offset) }
            let kept = bySize.dropLast(mergeCount).map(\.element)
            let members = bySize.suffix(mergeCount).map(\.element)
            rows += kept.map(DisplayRow.group)
            rows.append(merge(members))
        } else {
            rows += repos.map(DisplayRow.group)
        }
        // sorted is not stable: the offset keeps equal sizes in the CLI's order.
        return rows.enumerated()
            .sorted { ($0.element.bytes, $1.offset) > ($1.element.bytes, $0.offset) }
            .map(\.element)
    }

    /// `members` come biggest first.
    private static func merge(_ members: [SessionGroup]) -> DisplayRow {
        let others = members.dropFirst().map(\.name).joined(separator: " · ")
        return .merged(title: "\(members[0].name) 외 \(members.count - 1)개 프로젝트", subtitle: others, members: members)
    }

    /// The id of the row to open at first: the biggest repo, never `outside`
    /// or `unknown`.
    public static func initiallyExpanded(_ rows: [DisplayRow]) -> String? {
        for row in rows {
            if case .group(let group) = row, !fixedKeys.contains(group.key) { return group.id }
        }
        return nil
    }
}
