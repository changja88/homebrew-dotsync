import Foundation
import Testing
@testable import DotsyncKit

private let cleanupLine = "정리가 끝나면 꺼져요"

@Test func quitRulesKeepTheWindowForLongWork() {
    let now = ContinuousClock.now
    #expect(QuitRules.reply(longWork: cleanupLine, isWorking: true, quitBy: nil, now: now, poweringOff: false)
        == .waitVisible)
    #expect(QuitRules.reply(longWork: nil, isWorking: true, quitBy: nil, now: now, poweringOff: false)
        == .waitHidden(.seconds(30)))
    // A logout or restart never waits on the window: the CLI's stop is all.
    #expect(QuitRules.reply(longWork: cleanupLine, isWorking: true, quitBy: nil, now: now, poweringOff: true) == .now)
    #expect(QuitRules.reply(longWork: nil, isWorking: true, quitBy: now + .seconds(10), now: now, poweringOff: true)
        == .now)
    #expect(QuitRules.reply(longWork: nil, isWorking: false, quitBy: nil, now: now, poweringOff: false) == .now)
}

@Test func quitRulesLetAHiddenWaitRunOut() {
    let now = ContinuousClock.now
    let later = now + .seconds(10)
    // A quit asked again while the unseen wait runs: that wait quits later.
    #expect(QuitRules.reply(longWork: nil, isWorking: true, quitBy: later, now: now, poweringOff: false)
        == .alreadyWaiting)
    #expect(QuitRules.reply(longWork: nil, isWorking: true, quitBy: now, now: later, poweringOff: false) == .now)
    #expect(QuitRules.reply(longWork: nil, isWorking: false, quitBy: later, now: now, poweringOff: false) == .now)
    // Long work that began meanwhile (the window came back) is waited for in it.
    #expect(QuitRules.reply(longWork: cleanupLine, isWorking: true, quitBy: now, now: later, poweringOff: false)
        == .waitVisible)
}

@Test func longWorkLineNamesWhatTheAppWaitsFor() {
    #expect(QuitRules.longWork(sessionWork: true, applying: false) == "정리가 끝나면 꺼져요")
    #expect(QuitRules.longWork(sessionWork: false, applying: true) == "작업이 끝나면 꺼져요")
    // Both: the app quits only after the apply too, so not "정리가 끝나면".
    #expect(QuitRules.longWork(sessionWork: true, applying: true) == "작업이 끝나면 꺼져요")
    #expect(QuitRules.longWork(sessionWork: false, applying: false) == nil)
}

@Test func quitNoticeShowsEachRunsProgress() {
    #expect(QuitRules.notice(cleanupLine, runs: [(label: "지우는 중…", done: 3, total: 10)])
        == "정리가 끝나면 꺼져요 · 지우는 중… 3/10")
    // Before the CLI's first progress line there is no count to show.
    #expect(QuitRules.notice(cleanupLine, runs: [(label: "Codex 정리 중…", done: 0, total: 0)])
        == "정리가 끝나면 꺼져요 · Codex 정리 중…")
    #expect(QuitRules.notice("작업이 끝나면 꺼져요", runs: [
        (label: "되돌리는 중…", done: 0, total: 1), (label: "지우는 중…", done: 2, total: 4),
    ]) == "작업이 끝나면 꺼져요 · 되돌리는 중… 0/1 · 지우는 중… 2/4")
    #expect(QuitRules.notice("작업이 끝나면 꺼져요", runs: []) == "작업이 끝나면 꺼져요")
}

@Test func quitRulesTimings() {
    #expect(QuitRules.hiddenWait == .seconds(30))
    #expect(QuitRules.powerOffStop == .seconds(20))
}
