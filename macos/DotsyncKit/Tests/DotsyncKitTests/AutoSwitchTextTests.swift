import Foundation
import Testing
@testable import DotsyncKit

private let t0 = Date(timeIntervalSince1970: 1_793_016_000)  // 2026-10-26 12:00 UTC

private func entry(_ result: AutoSwitchResult, from: String? = "a", to: String? = nil, reason: String? = nil,
                   message: String? = nil) -> AutoSwitchLogEntry {
    AutoSwitchLogEntry(at: t0, from: from, to: to, reason: reason, rule: .even, result: result, message: message)
}

@Test func aSwitchIsAnnouncedWithLabelsAndTheReason() {
    let notice = AutoSwitchText.notification(for: entry(.switched, to: "b", reason: "5시간 92%"), previous: nil,
                                             labels: ["a": "에이"])
    #expect(notice == AutoSwitchNotice(title: "자동 교체", body: "에이 → b (5시간 92%)"))
}

@Test func noCandidateIsAnnouncedOnce() {
    let full = entry(.noCandidate, reason: "5시간 92%")
    let notice = AutoSwitchNotice(title: "자동 교체", body: "바꿀 계정이 없어요 — 모든 계정이 기준을 넘었어요")
    #expect(AutoSwitchText.notification(for: full, previous: nil, labels: [:]) == notice)
    #expect(AutoSwitchText.notification(for: full, previous: entry(.switched, to: "b"), labels: [:]) == notice)
    // Every 15 minutes while all stay full: once is enough.
    #expect(AutoSwitchText.notification(for: full, previous: full, labels: [:]) == nil)
}

@Test func otherResultsAreNotAnnounced() {
    for result in [AutoSwitchResult.belowLimit, .unsavedLogin, .failed] {
        #expect(AutoSwitchText.notification(for: entry(result, message: "m"), previous: nil, labels: [:]) == nil)
    }
}

// MARK: the menu's status line

private let on = AutoSwitchSettings(enabled: true, rule: .even, excluded: [])
private let saved = UsageFile(version: 1, fetchedAt: t0, lastError: nil, active: "a", unsavedSeat: nil, accounts: [])
private let live = LiveUsage(updatedAt: t0, fiveHour: nil, sevenDay: nil)

private func status(_ settings: AutoSwitchSettings = on, agent: AgentState = .on, file: UsageFile = saved,
                    live: LiveUsage? = live) -> String {
    AutoSwitchText.status(settings: settings, agent: agent, file: file, live: live, now: t0, timeZone: seoul)
}

@Test func statusSaysOff() {
    #expect(status(.off, agent: .off) == "꺼져 있어요")
}

@Test func statusAsksForApproval() {
    #expect(status(agent: .needsApproval) == "시스템 설정에서 dotsync의 백그라운드 실행을 허용해 주세요")
}

@Test func statusSaysTheAgentIsGone() {
    #expect(status(agent: .notInstalled) == "검사 등록이 풀렸어요 — 껐다 다시 켜 주세요")
}

@Test func statusExplainsMissingLiveUsage() {
    #expect(status(live: nil)
        == "실시간 사용량이 들어오지 않아요 — claude-hud 설정에 display.externalUsageWritePath를 넣어 주세요")
}

@Test func statusWaitsForAnUnsavedLogin() {
    var unsaved = saved
    unsaved.active = nil
    unsaved.unsavedSeat = UnsavedSeat(email: "new@x", status: .ok)
    #expect(status(file: unsaved) == "저장 안 된 로그인을 쓰는 중이에요 — 저장하면 다시 지켜봐요")
}

@Test func statusShowsTheRestAndItsEnd() {
    var resting = on
    resting.restUntil = t0.addingTimeInterval(900)  // 21:15 in Seoul
    resting.restReason = .noCandidate
    #expect(status(resting) == "쉬는 중이에요 — 바꿀 계정이 없어서, 21:15까지")
    resting.restReason = .failed
    #expect(status(resting) == "쉬는 중이에요 — 교체에 실패해서, 21:15까지")
    resting.restReason = .belowLimit
    #expect(status(resting) == "쉬는 중이에요 — 조회해 보니 기준 아래여서, 21:15까지")
    resting.restUntil = t0
    #expect(status(resting) == "켜져 있어요 · 지켜보는 중")
}

@Test func statusSaysWatching() {
    #expect(status() == "켜져 있어요 · 지켜보는 중")
}

// MARK: the menu's log

@Test func logLinesSayWhatHappened() {
    let labels = ["a": "에이"]
    #expect(AutoSwitchText.logLine(entry(.switched, to: "b", reason: "5시간 92%"), labels: labels)
        == "에이 → b · 5시간 92%")
    #expect(AutoSwitchText.logLine(entry(.noCandidate, reason: "5시간 92%"), labels: labels)
        == "바꿀 계정 없음 · 5시간 92%")
    #expect(AutoSwitchText.logLine(entry(.belowLimit), labels: labels) == "조회해 보니 기준 아래")
    #expect(AutoSwitchText.logLine(entry(.unsavedLogin), labels: labels) == "저장 안 된 로그인이라 멈춤")
    #expect(AutoSwitchText.logLine(entry(.failed, message: "boom"), labels: labels) == "실패: boom")
}
