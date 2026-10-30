import Foundation
import Testing
@testable import DotsyncKit

private func core(user: UInt32 = 0, system: UInt32 = 0, idle: UInt32 = 0, nice: UInt32 = 0) -> CoreTicks {
    CoreTicks(user: user, system: system, idle: idle, nice: nice)
}

@Test func cpuIsTheBusyShareOfAllCoresBetweenTwoReads() {
    let before = [core(user: 100, idle: 100), core(system: 50, idle: 50)]
    let after = [core(user: 130, idle: 170), core(system: 70, idle: 130)]  // 30 + 20 busy of 200
    #expect(SystemMeters.cpuPercent(from: before, to: after) == 25)
}

@Test func cpuCountsNiceAsBusy() {
    #expect(SystemMeters.cpuPercent(from: [core()], to: [core(idle: 50, nice: 50)]) == 50)
}

@Test func cpuSurvivesACounterThatWrappedAround() {
    let before = [core(user: .max - 9, idle: .max - 9)]
    let after = [core(user: 10, idle: 10)]  // 20 busy of 40, not billions
    #expect(SystemMeters.cpuPercent(from: before, to: after) == 50)
}

@Test func cpuIsUnknownWhenNoTimePassedOrTheCoresChanged() {
    let cores = [core(user: 1, idle: 1)]
    #expect(SystemMeters.cpuPercent(from: cores, to: cores) == nil)
    #expect(SystemMeters.cpuPercent(from: cores, to: cores + cores) == nil)
    #expect(SystemMeters.cpuPercent(from: [], to: []) == nil)
}

@Test func ramIsAppMemoryWiredAndCompressedOfTheWholeMachine() {
    // Activity Monitor's 사용된 메모리: (internal - purgeable) + wired + compressed.
    let pages = MemoryPages(internalPages: 50, purgeable: 10, wired: 30, compressed: 20)
    #expect(SystemMeters.ramPercent(pages, pageSize: 1_000, physicalMemory: 200_000) == 45)
}

@Test func ramIsUnknownWhenPurgeableExceedsInternal() {
    let pages = MemoryPages(internalPages: 5, purgeable: 10, wired: 30, compressed: 20)
    #expect(SystemMeters.ramPercent(pages, pageSize: 1_000, physicalMemory: 100_000) == nil)
}

@Test func ramIsUnknownWhenTheNumbersCannotBeRight() {
    let pages = MemoryPages(internalPages: 50, purgeable: 0, wired: 60, compressed: 0)
    #expect(SystemMeters.ramPercent(pages, pageSize: 1_000, physicalMemory: 100_000) == nil)  // 110 %
    #expect(SystemMeters.ramPercent(pages, pageSize: 1_000, physicalMemory: 0) == nil)
}

@Test func gpuIsReadFromTheAcceleratorsStatistics() {
    #expect(SystemMeters.gpuPercent(in: ["Device Utilization %": 63, "Tiler Utilization %": 1]) == 63)
    #expect(SystemMeters.gpuPercent(in: ["Device Utilization %": 0]) == 0)
}

@Test func gpuIsUnknownWhenItsValueIsMissingOrOutOfRange() {
    #expect(SystemMeters.gpuPercent(in: [:]) == nil)
    #expect(SystemMeters.gpuPercent(in: ["Device Utilization %": "63"]) == nil)
    #expect(SystemMeters.gpuPercent(in: ["Device Utilization %": 101]) == nil)
    #expect(SystemMeters.gpuPercent(in: ["Device Utilization %": -1]) == nil)
}

@Test func twoGPUReadsKeepTheLargerBecauseAReadRightAfterAnotherSaysZero() {
    #expect(SystemMeters.larger(0, 33) == 33)
    #expect(SystemMeters.larger(33, 0) == 33)
    #expect(SystemMeters.larger(nil, 20) == 20)
    #expect(SystemMeters.larger(20, nil) == 20)
    #expect(SystemMeters.larger(nil, nil) == nil)
}

@Test func readingThisMachineGivesThreePercents() async {
    let stats = await SystemReader.read()
    for value in [stats.cpu, stats.gpu, stats.ram] {
        #expect(value.map { (0...100).contains($0) } == true)
    }
    #expect((stats.ram ?? 0) > 0)  // a running Mac always uses some
}

/// Counts how many times the shared work really ran.
private actor Runs {
    var count = 0

    func next() -> Int {
        count += 1
        return count
    }
}

@Test func readersAtTheSameTimeShareOneRun() async throws {
    let runs = Runs()
    let shared = SharedWork<Int> {
        let run = await runs.next()
        try? await Task.sleep(for: .milliseconds(100))
        return run
    }
    async let first = shared.value()
    async let second = shared.value()
    async let third = shared.value()
    let answers = await [first, second, third]
    #expect(answers == [1, 1, 1])
    #expect(await shared.value() == 2)  // over: the next reader runs it again
}

@Test func aReaderSoonAfterGetsTheSameRunAndALaterOneANewRun() async throws {
    let runs = Runs()
    let shared = SharedWork<Int>(keep: .milliseconds(300)) { await runs.next() }
    #expect(await shared.value() == 1)
    #expect(await shared.value() == 1)
    try await Task.sleep(for: .milliseconds(400))
    #expect(await shared.value() == 2)
}
