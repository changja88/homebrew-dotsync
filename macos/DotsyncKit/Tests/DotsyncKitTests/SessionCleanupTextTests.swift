import Foundation
import Testing
@testable import DotsyncKit

@Test func cleanupRefusalCopyByCode() {
    func line(_ code: String, _ message: String = "raw") -> String {
        SessionText.cleanupRefusal(CLIError(code: code, message: message))
    }
    #expect(line("codex_running") == "Codex가 실행 중이라 정리하지 않았어요")
    #expect(line("codex_version") == "지금 Codex 기록이 dotsync가 아는 형태가 아니라서 정리할 수 없어요")
    #expect(line("incomplete_scan") == "읽지 못한 대화 기록이 있어서 정리하지 않았어요")
    #expect(line("codex_db_failed") == "Codex 저장소를 읽지 못해서 정리하지 않았어요")
    #expect(line("busy") == "다른 Codex 작업이 끝나면 다시 해 주세요")
    #expect(line("something_else", "kept as is") == "kept as is")
}

@Test func cleanupItemResultLines() {
    func result(_ outcome: String, count: Int = 5, reason: String? = nil, message: String? = nil,
                lines: Int? = nil) -> CleanupItemResult {
        CleanupItemResult(id: "history_lines", outcome: outcome, count: count, lines: lines, reason: reason, message: message)
    }
    #expect(SessionText.cleanupResultLine(result("done")) == "5개 정리했어요")
    #expect(SessionText.cleanupResultLine(result("done", lines: 12)) == "5개 정리했어요 · 12줄")
    #expect(SessionText.cleanupResultLine(result("skipped", count: 0, reason: "codex_started")) == "그사이 Codex가 켜져서 멈췄어요")
    #expect(SessionText.cleanupResultLine(result("skipped", count: 0, reason: "stopped")) == "멈춰서 하지 않았어요")
    #expect(SessionText.cleanupResultLine(result("failed", count: 0, message: "disk full")) == "하지 못했어요 · disk full")
    #expect(SessionText.cleanupResultLine(result("failed", count: 0)) == "하지 못했어요")
    #expect(SessionText.cleanupResultLine(result("skipped", count: 0, reason: "other")) == "하지 않았어요")
}

@Test func anItemThatStoppedPartwaySaysWhatItRemoved() {
    // history_rows goes one session at a time; its count is the sessions it finished.
    func rows(_ outcome: String, count: Int, reason: String? = nil, message: String? = nil) -> CleanupItemResult {
        CleanupItemResult(id: "history_rows", outcome: outcome, count: count, lines: nil, reason: reason, message: message)
    }
    #expect(SessionText.cleanupResultLine(rows("skipped", count: 3, reason: "stopped")) == "3개만 정리하고 멈췄어요")
    #expect(SessionText.cleanupResultLine(rows("failed", count: 3, message: "database is locked")) == "3개만 정리했어요 · database is locked")
}

@Test func vacuumLinesByStopReason() {
    func lines(_ why: String, after: Int64 = 6_100_000_000) -> [String] {
        SessionText.vacuumLines(VacuumResult(bytesBefore: 8_600_000_000, bytesAfter: after, stoppedBecause: why, message: nil))
    }
    let shrunk = "대화 저장소가 8.6 GB에서 6.1 GB로 줄었어요"
    #expect(lines("freelist_empty") == [shrunk])
    #expect(lines("time_cap") == [shrunk, "10분이 지나서 멈췄어요. 다음 정리 때 이어서 줄여요"])
    #expect(lines("busy") == [shrunk, "저장소가 바빠서 멈췄어요"])
    #expect(lines("blocker") == [shrunk, "Codex가 켜져서 멈췄어요"])
    #expect(lines("stopped") == [shrunk, "멈췄어요"])
    // Nothing shrank: no "줄었어요" line, only why.
    #expect(lines("no_progress", after: 8_600_000_000) == ["더 줄일 수 없었어요"])
    #expect(lines("error", after: 8_600_000_000) == ["빈 공간을 돌려받지 못했어요"])
    #expect(lines("freelist_empty", after: 8_600_000_000).isEmpty)
}

@Test func keptRowsAreFixedCopy() {
    #expect(SessionText.cleanupKept.map(\.name) == ["데스크톱 앱 상태", "첨부 파일", "생성 이미지", "로그"])
    #expect(SessionText.cleanupKept[2].reason == "다른 세션이 쓸 수 있어요")
}

@Test func progressLineNamesThePhase() {
    #expect(SessionText.progressLine(phase: "read", done: 1291, total: 5596) == "기록 파일 1,291 / 5,596개 읽는 중")
    #expect(SessionText.progressLine(phase: "delete", done: 1200, total: 2304) == "세션 1,200 / 2,304개 지우는 중")
    #expect(SessionText.progressLine(phase: nil, done: 1291, total: 5596) == "1,291 / 5,596")
    #expect(SessionText.progressLine(phase: "later", done: 1, total: 2) == "1 / 2")
}

@Test func vacuumProgressIsAWholePercentFlooredAndBounded() {
    func line(_ done: Int, _ total: Int) -> String { SessionText.progressLine(phase: "vacuum", done: done, total: total) }
    #expect(line(153_600, 825_114) == "빈 공간 돌려받는 중 18%")
    #expect(line(0, 825_114) == "빈 공간 돌려받는 중 0%")
    #expect(line(825_114, 825_114) == "빈 공간 돌려받는 중 100%")
    #expect(line(999, 825_114) == "빈 공간 돌려받는 중 0%")
    #expect(line(900_000, 825_114) == "빈 공간 돌려받는 중 100%")
    #expect(line(5, 0) == "빈 공간 돌려받는 중 0%")
}
