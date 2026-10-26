import Foundation
import Testing
@testable import DotsyncKit

let sessionNow = Date(timeIntervalSince1970: 1_800_000_000)
private let dayLength: TimeInterval = 86_400

func makeItem(_ id: String, state: String = "idle", daysAgo: Double = 10, bytes: Int64 = 100) -> SessionItem {
    SessionItem(
        id: id, title: id, folder: "f", cwd: "/f", lastActivity: sessionNow.addingTimeInterval(-daysAgo * dayLength),
        bytes: bytes, state: state, reason: nil, placeReason: nil, subagents: nil, children: nil, archived: nil)
}

func makeGroup(_ name: String, gb: Double, key: String? = nil, items: [SessionItem] = []) -> SessionGroup {
    SessionGroup(
        key: key ?? "/repos/\(name)", name: name, folders: 1, foldersDeleted: 0, sessions: items.count,
        bytes: Int64(gb * 1_000_000_000), running: 0, items: items)
}

func makeList(_ groups: [SessionGroup]) -> SessionList {
    SessionList(
        tool: .claude, checkedAt: sessionNow, totals: SessionTotals(sessions: 0, bytes: 0, running: 0, historyDbBytes: nil),
        groups: groups, agentsError: nil)
}

@Test func onlyIdleCanBeSelected() {
    #expect(SessionRules.isSelectable(makeItem("a")))
    for state in ["running", "unknown", "background", "desktop", "interrupted"] {
        #expect(!SessionRules.isSelectable(makeItem("a", state: state)), "\(state)")
    }
}

@Test func quickSelectOnlyTakesIdle() {
    let items = [
        makeItem("idle-old", daysAgo: 6),
        makeItem("idle-new", daysAgo: 4),
        makeItem("idle-edge", daysAgo: 5),
    ] + ["unknown", "running", "background", "desktop", "interrupted"].map { makeItem($0, state: $0, daysAgo: 6) }
    let picked = SessionRules.quickSelect(makeList([makeGroup("g", gb: 1, items: items)]), olderThanDays: 5, now: sessionNow)
    #expect(picked == ["idle-old"])
}

@Test func selectableInGroupIsIdleIds() {
    let g = makeGroup("g", gb: 1, items: [makeItem("a"), makeItem("b", state: "running"), makeItem("c")])
    #expect(SessionRules.selectable(in: g) == ["a", "c"])
}

@Test func rowsMergeLikeTheClaudeBoard() {
    let groups = [
        makeGroup("spring_dream_server", gb: 25.2), makeGroup("fortune_master", gb: 3.5), makeGroup("dddjango", gb: 3.4),
        makeGroup("pully", gb: 0.6), makeGroup("spring-src", gb: 0.05), makeGroup("kkebi", gb: 0.03), makeGroup("admin", gb: 0.01),
        makeGroup("프로젝트 밖", gb: 0.7 - 0.1, key: "outside"), makeGroup("원본 미확인", gb: 0.024, key: "unknown"),
    ]
    let rows = SessionRules.rows(groups)
    #expect(rows.count == 6)
    guard case .group(let first) = rows[0] else { Issue.record("first row is not a group"); return }
    #expect(first.name == "spring_dream_server")
    guard case .merged(let title, let subtitle, let members) = rows[3] else { Issue.record("no merged row"); return }
    #expect(title == "pully 외 3개 프로젝트")
    #expect(subtitle == "spring-src · kkebi · admin")
    #expect(members.count == 4)
    guard case .group(let outside) = rows[4], case .group(let unknown) = rows[5] else { Issue.record("tail rows"); return }
    #expect((outside.key, unknown.key) == ("outside", "unknown"))
    #expect(rows[3].bytes == members.reduce(0) { $0 + $1.bytes })
    #expect(rows[3].id != rows[0].id)
}

@Test func mergedRowCountsItsMembers() {
    let a = makeGroup("a", gb: 1, items: [makeItem("1"), makeItem("2")])
    let b = SessionGroup(key: "/b", name: "b", folders: 1, foldersDeleted: 0, sessions: 3, bytes: 5, running: 2, items: [])
    let c = makeGroup("c", gb: 0.1)
    let rows = SessionRules.rows([a, b, c], limit: 2)
    #expect(rows.count == 2)
    let merged = rows.first { if case .merged = $0 { true } else { false } }
    #expect(merged?.sessions == 3)
    #expect(merged?.running == 2)
}

@Test func rowsNeverMergeOutsideOrUnknown() {
    let groups = [
        makeGroup("outside", gb: 0.1, key: "outside"), makeGroup("unknown", gb: 0.1, key: "unknown"), makeGroup("r", gb: 1),
    ]
    #expect(SessionRules.rows(groups, limit: 1).count == 3)
}

@Test func rowsKeepFiveCodexGroups() {
    let groups = [
        makeGroup("spring_dream_server", gb: 1.9), makeGroup("pully", gb: 0.302), makeGroup("fortune_master", gb: 0.255),
        makeGroup("dddjango", gb: 0.173), makeGroup("프로젝트 밖", gb: 2.6, key: "outside"),
    ]
    let rows = SessionRules.rows(groups)
    #expect(rows.count == 5)
    let names = rows.compactMap { row -> String? in if case .group(let g) = row { g.name } else { nil } }
    #expect(names == ["프로젝트 밖", "spring_dream_server", "pully", "fortune_master", "dddjango"])
}

@Test func initiallyExpandedIsLargestRepo() {
    let rows = SessionRules.rows([
        makeGroup("프로젝트 밖", gb: 2.6, key: "outside"), makeGroup("spring_dream_server", gb: 1.9), makeGroup("pully", gb: 0.3),
    ])
    #expect(SessionRules.initiallyExpanded(rows) == "/repos/spring_dream_server")
    #expect(SessionRules.initiallyExpanded(SessionRules.rows([makeGroup("o", gb: 1, key: "outside")])) == nil)
}

@Test func visibleItemsCapAtFive() {
    let g = makeGroup("g", gb: 1, items: (1...8).map { makeItem("\($0)") })
    #expect(SessionRules.visibleItems(g, showAll: false).map(\.id) == ["1", "2", "3", "4", "5"])
    #expect(SessionRules.visibleItems(g, showAll: true).count == 8)
    #expect(SessionRules.visibleItems(makeGroup("s", gb: 1, items: [makeItem("1")]), showAll: false).count == 1)
}

@Test func selectionTotalsCountOnlySelectableListedIds() {
    let l = makeList([makeGroup("g", gb: 1, items: [makeItem("a", bytes: 10), makeItem("b", bytes: 20), makeItem("c", state: "running", bytes: 40)])])
    let totals = SessionRules.selectionTotals(["a", "b", "c", "gone"], in: l)
    #expect((totals.count, totals.bytes) == (2, 30))
}

@Test func mergedRowIdIsStable() {
    func mergedID(_ names: [String]) -> String? {
        let groups = names.enumerated().map { makeGroup($1, gb: 10 - Double($0)) }
        return SessionRules.rows(groups, limit: 2).first { if case .merged = $0 { true } else { false } }?.id
    }
    #expect(mergedID(["a", "b", "c"]) == "merged")
    #expect(mergedID(["a", "b", "c", "d", "e"]) == "merged")
}

@Test func prunedKeepsOnlyListedSelectableIds() {
    let l = makeList([
        makeGroup("g", gb: 1, items: [makeItem("a"), makeItem("b", state: "running"), makeItem("c")]),
        makeGroup("h", gb: 1, items: [makeItem("d", state: "interrupted"), makeItem("e")]),
    ])
    #expect(SessionRules.pruned(["a", "b", "d", "gone", "e"], in: l) == ["a", "e"])
    #expect(SessionRules.pruned([], in: l).isEmpty)
}

@Test func mergedRowListsItsMembersItemsNewestFirst() {
    let a = makeGroup("a", gb: 3, items: [makeItem("a-old", daysAgo: 9), makeItem("a-new", daysAgo: 1)])
    let b = makeGroup("b", gb: 2, items: [makeItem("b-mid", daysAgo: 4)])
    let c = makeGroup("c", gb: 1, items: [makeItem("c-run", state: "running", daysAgo: 2)])
    let rows = SessionRules.rows([a, b, c], limit: 1)
    #expect(rows.count == 1)
    #expect(rows[0].items.map(\.id) == ["a-new", "c-run", "b-mid", "a-old"])
    #expect(SessionRules.selectable(in: rows[0]) == ["a-new", "b-mid", "a-old"])
    // A plain group keeps the CLI's order.
    let plain = SessionRules.rows([a])[0]
    #expect(plain.items.map(\.id) == ["a-old", "a-new"])
}

@Test func visibleItemsOfARowCapAtFive() {
    let g = makeGroup("g", gb: 1, items: (1...8).map { makeItem("\($0)") })
    let row = SessionRules.rows([g])[0]
    #expect(SessionRules.visibleItems(row, showAll: false).count == 5)
    #expect(SessionRules.visibleItems(row, showAll: true).count == 8)
}

@Test func groupCheckboxStateFollowsTheSelection() {
    let ids: Set<String> = ["a", "b"]
    #expect(SessionRules.checkState(["x"], among: ids) == .none)
    #expect(SessionRules.checkState(["a", "x"], among: ids) == .some)
    #expect(SessionRules.checkState(["a", "b"], among: ids) == .all)
    // Nothing to pick: never checked.
    #expect(SessionRules.checkState(["a"], among: []) == .none)
}

@Test func togglingAGroupSelectsAllOrClearsAll() {
    let ids: Set<String> = ["a", "b"]
    #expect(SessionRules.togglingGroup(["x"], selectable: ids) == ["x", "a", "b"])
    #expect(SessionRules.togglingGroup(["a", "x"], selectable: ids) == ["x", "a", "b"])
    #expect(SessionRules.togglingGroup(["a", "b", "x"], selectable: ids) == ["x"])
    #expect(SessionRules.togglingGroup(["x"], selectable: []) == ["x"])
}

@Test func itemLookupFindsListedIdsOnly() {
    let l = makeList([makeGroup("g", gb: 1, items: [makeItem("a")]), makeGroup("h", gb: 1, items: [makeItem("b", state: "running")])])
    #expect(SessionRules.item("b", in: l)?.state == "running")
    #expect(SessionRules.item("gone", in: l) == nil)
}

// MARK: A delete's result applied to the list

private func codexList(_ groups: [SessionGroup]) -> SessionList {
    var list = makeList(groups)
    list.tool = .codex
    list.totals = SessionTotals(
        sessions: groups.reduce(0) { $0 + $1.sessions }, bytes: groups.reduce(0) { $0 + $1.bytes },
        running: groups.reduce(0) { $0 + $1.running }, historyDbBytes: 8_600_000_000)
    return list
}

private func group(_ key: String, _ items: [SessionItem], folders: Int = 2, deleted: Int = 1) -> SessionGroup {
    SessionGroup(key: key, name: key, folders: folders, foldersDeleted: deleted, sessions: items.count,
                 bytes: items.reduce(0) { $0 + $1.bytes }, running: items.filter { $0.state == "running" }.count, items: items)
}

private func report(_ outcomes: [String: String]) -> DeleteReport {
    DeleteReport(tool: .codex, results: outcomes.sorted { $0.key < $1.key }.map {
        DeleteResult(id: $0.key, outcome: $0.value, reason: nil, kept: nil, failed: nil, freedBytes: nil,
                     children: nil, remaining: nil, unchecked: nil, message: nil)
    }, stopped: false)
}

@Test func applyingDropsDeletedItemsAndRecountsTotals() {
    // A Codex root with children counts once, as the CLI counts it (codex.py
    // list_sessions: totals.sessions is len(items), one per root thread).
    var withChildren = makeItem("b", bytes: 300)
    withChildren.children = 4
    let list = codexList([
        group("g1", [makeItem("a", bytes: 100), withChildren, makeItem("r", state: "running", bytes: 50)]),
        group("g2", [makeItem("c", bytes: 700)]),
    ])
    let after = SessionRules.applying(report(["a": "deleted", "c": "deleted"]), to: list)
    #expect(after.groups.map(\.key) == ["g1"])
    #expect(after.groups[0].items.map(\.id) == ["b", "r"])
    #expect(after.groups[0].sessions == 2)
    #expect(after.groups[0].bytes == 350)
    #expect(after.groups[0].running == 1)
    #expect(after.totals == SessionTotals(sessions: 2, bytes: 350, running: 1, historyDbBytes: 8_600_000_000))
    // What the delete does not change stays.
    #expect(after.groups[0].folders == 2 && after.groups[0].foldersDeleted == 1)
    #expect(after.checkedAt == list.checkedAt)
}

@Test func applyingKeepsSkippedAndPartial() {
    let list = codexList([group("g1", [makeItem("a"), makeItem("b"), makeItem("c"), makeItem("d")])])
    let after = SessionRules.applying(
        report(["a": "skipped", "b": "partial", "c": "restored", "d": "failed", "zz": "deleted"]), to: list)
    #expect(after == list)
}

@Test func applyingDropsAnEmptiedGroup() {
    let list = codexList([group("g1", [makeItem("a")]), group("g2", [makeItem("b")])])
    let after = SessionRules.applying(report(["a": "deleted"]), to: list)
    #expect(after.groups.map(\.key) == ["g2"])
    #expect(after.totals.sessions == 1)
    let none = SessionRules.applying(report(["a": "deleted", "b": "deleted"]), to: list)
    #expect(none.groups.isEmpty)
    #expect(none.totals == SessionTotals(sessions: 0, bytes: 0, running: 0, historyDbBytes: 8_600_000_000))
}
