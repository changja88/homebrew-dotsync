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
