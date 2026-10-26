import Testing
@testable import DotsyncKit

@Test func countsOnlyGrowWithinAPhase() {
    var progress = RunProgress()
    progress.advance(done: 5, total: 240, phase: nil)
    // Lines hop to the main actor one by one and may land out of order.
    progress.advance(done: 3, total: 240, phase: nil)
    #expect(progress == RunProgress(done: 5, total: 240, phase: nil))
    #expect(progress.text == "5/240")
}

@Test func aNewPhaseStartsCountingAgain() {
    var progress = RunProgress()
    progress.advance(done: 5596, total: 5596, phase: "read")
    #expect(progress.text == "기록 파일 5,596 / 5,596개 읽는 중")
    progress.advance(done: 1, total: 2304, phase: "delete")
    #expect(progress == RunProgress(done: 1, total: 2304, phase: "delete"))
    progress.advance(done: 153_600, total: 825_114, phase: "vacuum")
    #expect(progress.text == "빈 공간 돌려받는 중 18%")
}

@Test func aLateLineOfAnEarlierPhaseIsIgnored() {
    var progress = RunProgress()
    progress.advance(done: 10, total: 2304, phase: "delete")
    progress.advance(done: 5596, total: 5596, phase: "read")
    #expect(progress == RunProgress(done: 10, total: 2304, phase: "delete"))
}

@Test func beforeTheFirstLineThereIsNothingToShow() {
    #expect(!RunProgress().hasCount)
    var progress = RunProgress()
    progress.advance(done: 0, total: 0, phase: nil)
    #expect(progress.hasCount)  // total 0 is shown as 1: a bar needs a range
    #expect(progress.total == 1)
}
