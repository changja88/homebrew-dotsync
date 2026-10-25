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
