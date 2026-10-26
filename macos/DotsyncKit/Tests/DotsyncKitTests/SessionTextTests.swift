import Foundation
import Testing
@testable import DotsyncKit

private func textItem(
    state: String = "idle", reason: String? = nil, placeReason: String? = nil, folder: String = "lane-10-1-2",
    subagents: Int? = nil, children: Int? = nil, archived: Bool? = nil
) -> SessionItem {
    SessionItem(
        id: "i", title: "t", folder: folder, cwd: "/f", lastActivity: sessionNow, bytes: 1, state: state,
        reason: reason, placeReason: placeReason, subagents: subagents, children: children, archived: archived)
}

private func result(
    _ outcome: String, reason: String? = nil, kept: [String]? = nil, children: Int? = nil, unchecked: [String]? = nil
) -> DeleteResult {
    DeleteResult(
        id: "r", outcome: outcome, reason: reason, kept: kept, failed: nil, freedBytes: nil, children: children,
        remaining: nil, unchecked: unchecked, message: nil)
}

@Test func totalsMatchTheBoards() {
    let totals = SessionTotals(sessions: 244, bytes: 33_400_000_000, running: 11, historyDbBytes: nil)
    let claude = SessionList(tool: .claude, checkedAt: sessionNow, totals: totals, groups: [])
    #expect(SessionText.totals(claude) == "244개 · 33.4 GB · 실행 중 11")
    var codex = claude
    codex.totals = SessionTotals(sessions: 1725, bytes: 5_300_000_000, running: 3, historyDbBytes: nil)
    #expect(SessionText.totals(codex) == "1,725개 · 5.3 GB · 실행 중 3")
    codex.totals.running = 0
    #expect(SessionText.totals(codex) == "1,725개 · 5.3 GB")
}

@Test func bytesUseTheBoardsForm() {
    // Decimal units, a space, "." as the point: the same under any locale
    // (the function takes none and reads none).
    let cases: [(Int64, String)] = [
        (25_200_000_000, "25.2 GB"), (1_180_000_000, "1.2 GB"), (11_540_000_000, "11.5 GB"),
        (302_000_000, "302 MB"), (1_112_913, "1 MB"), (24_000, "24 KB"), (300, "1 KB"), (0, "0 KB"),
        // A value that rounds to 1,000 of a unit shows as 1 of the next.
        (999_600_000, "1.0 GB"), (999_500, "1 MB"), (999_999, "1 MB"),
        (999_499_999, "999 MB"), (1_000_000_000, "1.0 GB"), (1_000, "1 KB"),
    ]
    for (count, text) in cases {
        #expect(SessionText.bytes(count) == text, "\(count)")
    }
}

@Test func bytesUseFileStyle() {
    #expect(SessionText.bytes(24_000_000) == "24 MB")
    #expect(SessionText.bytes(8_600_000_000) == "8.6 GB")
}

@Test func relativeTimes() {
    func ago(_ seconds: TimeInterval) -> String {
        SessionText.relative(sessionNow.addingTimeInterval(-seconds), now: sessionNow)
    }
    #expect(ago(0) == "방금")
    #expect(ago(59) == "방금")
    #expect(ago(-30) == "방금")
    #expect(ago(60) == "1분 전")
    #expect(ago(59 * 60 + 59) == "59분 전")
    #expect(ago(3600) == "1시간 전")
    #expect(ago(23 * 3600) == "23시간 전")
    #expect(ago(7 * 86_400) == "7일 전")
}

@Test func chipsOfTheBoards() {
    #expect(SessionText.chip(textItem(state: "running")) == "실행 중")
    #expect(SessionText.chip(textItem(state: "unknown")) == "확인 못 함")
    #expect(SessionText.chip(textItem(state: "background")) == "agent view에 있음")
    #expect(SessionText.chip(textItem(state: "desktop")) == "Claude 앱 세션")
    #expect(SessionText.chip(textItem(state: "interrupted")) == "지우다 멈춤")
    #expect(SessionText.chip(textItem(state: "idle")) == nil)
}

@Test func subLinesJoinTheExtras() {
    #expect(SessionText.subLine(textItem(), tool: .claude) == "lane-10-1-2")
    #expect(SessionText.subLine(textItem(folder: "lane-admin-7", subagents: 14), tool: .claude)
        == "lane-admin-7 · 하위 에이전트 기록 14개")
    #expect(SessionText.subLine(textItem(folder: "lane-9-A-1b", children: 3), tool: .codex)
        == "lane-9-A-1b · 하위 세션 3개도 같이 지워져요")
    #expect(SessionText.subLine(textItem(subagents: 0, children: 0), tool: .codex) == "lane-10-1-2")
    #expect(SessionText.subLine(textItem(state: "unknown", reason: "recent"), tool: .claude)
        == "lane-10-1-2 · 30분 안에 기록됐어요")
    #expect(SessionText.subLine(textItem(archived: true), tool: .codex) == "lane-10-1-2 · 보관됨")
    for reason in ["agents_missing", "agents_failed", "agents_incomplete", "process_scan_failed"] {
        #expect(SessionText.subLine(textItem(state: "unknown", reason: reason), tool: .claude)
            == "lane-10-1-2 · 실행 목록을 읽지 못했어요")
    }
    #expect(SessionText.subLine(textItem(state: "unknown", reason: "unexplained_process"), tool: .claude)
        == "lane-10-1-2 · 같은 폴더에서 실행 중인 Claude가 있어요")
    #expect(SessionText.subLine(textItem(state: "unknown", reason: "scan_failed"), tool: .codex)
        == "lane-10-1-2 · 실행 여부를 확인하지 못했어요")
    #expect(SessionText.subLine(textItem(folder: "", children: 2), tool: .codex) == "하위 세션 2개도 같이 지워져요")
}

private func plainGroup(_ key: String, folders: Int = 1, deleted: Int = 0, reasons: [String?] = []) -> SessionGroup {
    SessionGroup(
        key: key, name: key, folders: folders, foldersDeleted: deleted, sessions: reasons.count, bytes: 0, running: 0,
        items: reasons.map { textItem(placeReason: $0) })
}

@Test func groupSubLinesMatchTheBoards() {
    #expect(SessionText.groupSubLine(plainGroup("/r", folders: 101, deleted: 96), selected: 2)
        == "작업 폴더 101개 · 96개는 지워져서 경로로 찾았어요 · 2개 선택")
    #expect(SessionText.groupSubLine(plainGroup("/r", folders: 1), selected: 0) == "작업 폴더 1개")
    #expect(SessionText.groupSubLine(plainGroup("outside"), selected: 0) == "임시 폴더처럼 저장소가 아닌 곳에서 연 세션")
    #expect(SessionText.groupSubLine(plainGroup("outside"), selected: 3)
        == "임시 폴더처럼 저장소가 아닌 곳에서 연 세션 · 3개 선택")
}

@Test func unknownGroupSubLineFollowsThePlaceReasons() {
    func line(_ reasons: [String?]) -> String { SessionText.groupSubLine(plainGroup("unknown", reasons: reasons), selected: 0) }
    #expect(line(["not_found", "not_found"]) == "폴더가 지워져서 어느 저장소인지 몰라요")
    #expect(line(["cwd_unreadable"]) == "폴더를 읽을 수 없어요")
    #expect(line(["git_failed", "git_failed"]) == "저장소인지 확인하지 못했어요")
    #expect(line(["not_found", "git_failed"]) == "어느 저장소인지 확인하지 못했어요")
    #expect(line(["not_found", nil]) == "어느 저장소인지 확인하지 못했어요")
    #expect(line([]) == "어느 저장소인지 확인하지 못했어요")
    #expect(SessionText.groupSubLine(plainGroup("unknown", reasons: ["not_found"]), selected: 2)
        == "폴더가 지워져서 어느 저장소인지 몰라요 · 2개 선택")
}

@Test func bottomRulesMatchTheBoards() {
    #expect(SessionText.bottomRule(tool: .claude, historyDbBytes: nil)
        == "실행 중이거나 방금 기록된 세션은 고를 수 없어요 · 크기는 세션 파일과 그 세션만 쓰는 폴더를 합친 값이에요")
    #expect(SessionText.bottomRule(tool: .codex, historyDbBytes: 8_600_000_000)
        == "실행 중이거나 확인 못 한 세션은 고를 수 없어요 · 크기에 공유 대화 저장소(8.6 GB)는 빠져요")
    #expect(SessionText.bottomRule(tool: .codex, historyDbBytes: nil)
        == "실행 중이거나 확인 못 한 세션은 고를 수 없어요 · 크기에 공유 대화 저장소는 빠져요")
}

private func report(_ outcomes: [String]) -> DeleteReport {
    DeleteReport(tool: .codex, results: outcomes.map { result($0) }, stopped: false)
}

@Test func resultTitles() {
    #expect(SessionText.resultTitle(report(["deleted", "deleted", "skipped"])) == "2개를 지웠고 1개는 건너뛰었어요")
    #expect(SessionText.resultTitle(report(["deleted", "deleted"])) == "2개를 지웠어요")
    #expect(SessionText.resultTitle(report(["skipped", "skipped"])) == "2개를 건너뛰었어요")
    #expect(SessionText.resultTitle(report(["deleted", "partial"])) == "1개를 지웠고 1개는 일부만 지웠어요")
    #expect(SessionText.resultTitle(report(["partial", "skipped"])) == "1개를 일부만 지웠고 1개는 건너뛰었어요")
    #expect(SessionText.resultTitle(report(["restored"])) == "1개를 되돌렸어요")
    #expect(SessionText.resultTitle(report([])) == "지운 게 없어요")
}

@Test func deletedAndRestoredLines() {
    #expect(SessionText.resultLine(result("deleted"), tool: .claude) == "지웠어요")
    #expect(SessionText.resultLine(result("deleted", children: 3), tool: .claude) == "지웠어요")
    #expect(SessionText.resultLine(result("deleted", children: 3), tool: .codex) == "하위 세션 3개까지 모두 지웠어요")
    #expect(SessionText.resultLine(result("deleted", children: 0), tool: .codex) == "지웠어요")
    #expect(SessionText.resultLine(result("deleted", kept: ["tool_results"]), tool: .claude)
        == "지웠어요. 다른 세션이 쓸 수 있는 도구 결과는 남겼어요")
    #expect(SessionText.resultLine(result("deleted", kept: ["memory"]), tool: .claude) == "지웠어요")
    #expect(SessionText.resultLine(result("restored"), tool: .claude) == "되돌렸어요. 다시 이어서 열 수 있어요")
}

@Test func partialLines() {
    #expect(SessionText.resultLine(result("partial"), tool: .claude) == "일부만 지웠을 수 있어요")
    #expect(SessionText.resultLine(result("partial", reason: "failed", unchecked: ["history"]), tool: .codex)
        == "일부만 지웠을 수 있어요. 남은 게 있는지 확인하지 못했어요")
    #expect(SessionText.resultLine(result("partial", reason: "failed", unchecked: []), tool: .codex) == "일부만 지웠을 수 있어요")
    #expect(SessionText.resultLine(result("partial", reason: "no_rollout"), tool: .codex)
        == "일부만 지웠을 수 있어요. 나머지는 \"남은 데이터 정리\"에서 지워져요")
    #expect(SessionText.resultLine(result("partial", reason: "something_new"), tool: .codex) == "일부만 지웠을 수 있어요")
}

@Test func skippedLinesMatchTheBriefAndRulings() {
    let expected: [String: String] = [
        // dotsync's own check skips it too, before Codex runs: the line must hold for both.
        "in_use": "다른 창에서 열려 있어서 지우지 않았어요",
        "referenced": "이 세션에서 갈라져 나온 세션이 아직 이 기록을 써서 지울 수 없어요",
        "stopped": "멈춰서 지우지 않았어요",
        "running": "그사이 다시 열려서 건너뛰었어요",
        "unknown:new_transcript": "그사이 새로 기록돼서 건너뛰었어요",
        "unknown:recent": "방금 기록돼서 건너뛰었어요",
        "unknown:unsafe_path": "경로가 안전하지 않아서 건드리지 않았어요",
        "unknown:unexplained_process": "같은 폴더에서 실행 중인 Claude가 있어서 건너뛰었어요",
        "unknown:agents_missing": "실행 여부를 확인하지 못해서 건너뛰었어요",
        "unknown:agents_failed": "실행 여부를 확인하지 못해서 건너뛰었어요",
        "unknown:agents_incomplete": "실행 여부를 확인하지 못해서 건너뛰었어요",
        "unknown:process_scan_failed": "실행 여부를 확인하지 못해서 건너뛰었어요",
        "unknown:something_new": "실행 여부를 확인하지 못해서 건너뛰었어요",
        "not_found": "이미 없어요",
        "refused": "Codex가 지우지 않았어요. 대화 기록은 그대로예요",
        "invalid_id": "id 형식이 달라서 건드리지 않았어요",
        "codex_missing": "Codex 프로그램을 찾지 못해서 지우지 않았어요",
        "scan_failed": "사용 중인지 확인하지 못해서 건너뛰었어요",
        "unsafe_path": "경로가 안전하지 않아서 건드리지 않았어요",
        "inspect_failed": "남은 기록을 확인하지 못해서 건너뛰었어요",
        "not_root": "하위 세션이라 따로 지우지 않았어요",
        "rename_failed": "파일을 옮기지 못해서 건너뛰었어요",
        "recent": "방금 기록돼서 건너뛰었어요",
        "background": "agent view에 있어서 건너뛰었어요",
        "desktop": "Claude 앱 세션이라 건너뛰었어요",
    ]
    for (reason, line) in expected {
        #expect(SessionText.resultLine(result("skipped", reason: reason), tool: .codex) == line, "\(reason)")
    }
    #expect(SessionText.resultLine(result("skipped", reason: "brand_new"), tool: .claude) == "건너뛰었어요")
    #expect(SessionText.resultLine(result("skipped"), tool: .claude) == "건너뛰었어요")
}

/// Every reason lib/dotsync/sessions/{claude,claude_delete,codex}.py can put
/// on a skipped result. Add a new one here when the CLI gains it.
private let cliSkippedReasons = [
    // claude_delete.py: _delete_one, delete (stop), _not_deletable, restore
    "not_found", "rename_failed", "stopped", "running", "background", "desktop", "unknown:new_transcript",
    // claude.state_of reasons behind "unknown:<reason>"
    "unknown:unsafe_path", "unknown:agents_missing", "unknown:agents_failed", "unknown:agents_incomplete",
    "unknown:process_scan_failed", "unknown:recent", "unknown:unexplained_process",
    // codex.py: _delete_one, _not_now, _leftovers, _refusal, stop
    "codex_missing", "in_use", "invalid_id", "scan_failed", "unsafe_path", "inspect_failed", "not_root",
    "referenced", "refused",
    // the reasons the brief and rulings also name
    "recent",
]

@Test func noCLIReasonFallsToTheFallback() {
    for reason in cliSkippedReasons {
        let line = SessionText.resultLine(result("skipped", reason: reason), tool: .codex)
        #expect(line != SessionText.skippedFallback, "skipped \(reason)")
    }
}

@Test func failedRestoreHasItsOwnLine() {
    #expect(SessionText.resultLine(result("failed"), tool: .claude) == "되돌리지 못했어요. 지우다 멈춘 상태로 남아 있어요")
}

@Test func linesForTheCLIsOwnReports() throws {
    let claude = try UsageJSON.decoder().decode(DeleteReport.self, from: fixture("claude-delete-report"))
    #expect(claude.results.map { SessionText.resultLine($0, tool: .claude) } == [
        "지웠어요", "파일을 옮기지 못해서 건너뛰었어요", "일부만 지웠을 수 있어요", "그사이 다시 열려서 건너뛰었어요",
    ])
    #expect(SessionText.resultTitle(claude) == "1개를 지웠고 1개는 일부만 지웠고 2개는 건너뛰었어요")
    let codex = try UsageJSON.decoder().decode(DeleteReport.self, from: fixture("codex-delete-report"))
    #expect(codex.results.map { SessionText.resultLine($0, tool: .codex) } == [
        "하위 세션 2개까지 모두 지웠어요", "Codex가 지우지 않았어요. 대화 기록은 그대로예요",
        "일부만 지웠을 수 있어요. 남은 게 있는지 확인하지 못했어요",
    ])
}

@Test func selectionLineHasNoSizeWhenNothingIsPicked() {
    #expect(SessionText.selectionLine(count: 0, bytes: 0) == "0개 선택")
    #expect(SessionText.selectionLine(count: 2, bytes: 2_600_000_000) == "2개 선택 · \(SessionText.bytes(2_600_000_000))")
}

@Test func zeroBytesReadAsANumber() {
    #expect(!SessionText.bytes(0).contains("Zero"))
}

// MARK: Confirm and result copy

@Test func confirmCopyOfTheBoards() {
    #expect(SessionText.confirmTitle(tool: .claude, count: 2) == "Claude Code 세션 2개를 지울까요?")
    #expect(SessionText.confirmTitle(tool: .codex, count: 3) == "Codex 세션 3개를 지울까요?")
    #expect(SessionText.confirmSizeLine(tool: .claude, children: 0, bytes: 2_600_000_000)
        == "2.6 GB · 되돌릴 수 없어요. 이 세션은 다시 이어서 열 수 없게 돼요.")
    #expect(SessionText.confirmSizeLine(tool: .codex, children: 3, bytes: 500_000_000)
        == "하위 세션 3개 포함 · 500 MB · 되돌릴 수 없어요. Codex는 켜 둔 채로 지워요.")
    #expect(SessionText.confirmNote(tool: .codex, historyDbBytes: 8_600_000_000)
        == "대화 저장소(8.6 GB)의 빈 공간은 바로 줄지 않아요. \"남은 데이터 정리\"에서 돌려받을 수 있어요.")
    #expect(SessionText.confirmNote(tool: .codex, historyDbBytes: nil).hasPrefix("대화 저장소의 빈 공간은"))
    #expect(SessionText.confirmFinePrint(tool: .claude) == nil)
    #expect(SessionText.confirmFinePrint(tool: .codex) != nil)
}

@Test func confirmRowsSplitWhoDeletesWhat() {
    let codex = SessionText.confirmRows(tool: .codex, children: 3)
    #expect(codex.map(\.label) == ["Codex가 지움", "dotsync가 이어서", "남는 것"])
    #expect(codex[0].pills.last == "하위 세션 3개")
    #expect(codex[1].pills == ["참조 캐시 파일"])
    #expect(codex[2].pills.last == "생성 이미지 — 다른 세션이 쓸 수 있어요")
    #expect(!SessionText.confirmRows(tool: .codex, children: 0)[0].pills.contains { $0.hasPrefix("하위 세션") })
    let claude = SessionText.confirmRows(tool: .claude, children: 0)
    #expect(claude.map(\.label) == ["지우는 것", "남는 것"])
    #expect(claude[0].pills.count == 9)
    #expect(claude[1].pills.last == "다른 세션이 쓰는 도구 결과")
}

@Test func freedLineIsLeftOutForZero() {
    #expect(SessionText.freedLine(400_000_000) == "세션 파일 400 MB가 비었어요.")
    #expect(SessionText.freedLine(0) == nil)
    #expect(SessionText.progress(done: 12, total: 240) == "12/240")
}

@Test func failureLinesClaimNothingAboutWhatWasDeleted() {
    let check = "지워진 게 있는지 목록에서 확인해 주세요"
    #expect(SessionText.failureLine(CLIError(code: "cancelled", message: "dotsync was stopped")) == "멈췄어요. \(check)")
    #expect(SessionText.failureLine(CLIError(code: "timeout", message: "x")) == "시간이 너무 오래 걸려서 멈췄어요. \(check)")
    #expect(SessionText.failureLine(CLIError(code: "busy", message: "x")) == "다른 dotsync 작업이 끝나면 다시 해 주세요")
    #expect(SessionText.failureLine(CLIError(code: "config", message: "다른 작업 중")) == "다른 작업 중")
    #expect(SessionText.failureLine(NSError(domain: "x", code: 1)).isEmpty == false)
}
