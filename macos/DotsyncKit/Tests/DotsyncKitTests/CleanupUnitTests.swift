import Foundation
import os
import Testing
@testable import DotsyncKit

// MARK: Shapes

private func blocker(_ kind: String, _ name: String, pid: Int) -> Blocker {
    Blocker(pid: pid, kind: kind, name: name, elapsed: 720)
}

private let chatgptApp = blocker("chatgpt_app", "ChatGPT", pid: 10)
private let appServer = blocker("chatgpt_child", "codex", pid: 11)
private let computerUse = blocker("chatgpt_child", "SkyComputerUseService", pid: 12)
private let terminalCodex = blocker("terminal_codex", "codex", pid: 20)
private let otherProgram = blocker("other", "node", pid: 21)
private let detectionFailed = blocker("detection_failed", "lsof", pid: 0)

private func found(_ blockers: [Blocker], openThreads: Int = 0) -> CleanupBlockers {
    CleanupBlockers(blockers: blockers, chatgptHelpers: [], chatgptOpenThreads: openThreads)
}

private let chatgptOnly = found([chatgptApp, appServer])
private let nothingRuns = found([])

private func itemResult(_ outcome: String, reason: String? = nil) -> CleanupItemResult {
    CleanupItemResult(id: "cache_files", outcome: outcome, count: 3, lines: nil, reason: reason, message: nil)
}

private let cleaned = CleanupReport(items: [itemResult("done")], vacuum: nil, stopped: false)
private let stoppedRun = CleanupReport(items: [itemResult("skipped", reason: "stopped")], vacuum: nil, stopped: true)
private let codexRunning = CLIError(code: "codex_running", message: "quit what uses ~/.codex first: codex")
private let stoppedBeforeCleanup = CLIError(code: "cancelled", message: "dotsync was stopped")

private func preview(refusal: CLIError? = nil) -> CleanupPreview {
    CleanupPreview(
        blockers: [], chatgptHelpers: [], chatgptOpenThreads: 0, items: [], historyDbBytes: 0,
        vacuumAvailable: true, refusal: refusal)
}

// MARK: Fakes

/// UserDefaults' two calls, in memory.
private final class FakeFlags: FlagStore {
    private var values: [String: Bool] = [:]
    /// Every value written, in order.
    private(set) var writes: [Bool] = []

    func bool(forKey key: String) -> Bool { values[key] ?? false }

    func set(_ value: Bool, forKey key: String) {
        values[key] = value
        writes.append(value)
    }

    var quitChatGPT: Bool { bool(forKey: CleanupUnit.flagKey) }
}

/// ChatGPT.app, by what it answers. It records what the unit asked of it.
@MainActor
private final class FakeChatGPT: ChatGPTControl {
    var isRunning: Bool
    var terminateAnswer = true
    var quitsInTime = true
    /// The wait answers only when the test calls `endWait`.
    var holdsTheWait = false
    /// The relaunch answers, in order; past the end, true.
    var relaunchAnswers: [Bool] = []

    private let flags: FakeFlags
    private(set) var terminateCalls = 0
    private(set) var flagAtTerminate: Bool?
    private(set) var waitTimeouts: [Duration] = []
    private(set) var relaunchCalls = 0
    private(set) var flagAtRelaunch: [Bool] = []
    private(set) var relaunchSawCancel: [Bool] = []
    private var heldWait: CheckedContinuation<Bool, Never>?
    private var listeners: [AsyncStream<Void>.Continuation] = []
    /// Streams from `launches()` that have not ended yet.
    let openStreams = OSAllocatedUnfairLock(initialState: 0)

    init(running: Bool, flags: FakeFlags) {
        isRunning = running
        self.flags = flags
    }

    var isWaiting: Bool { heldWait != nil }

    func terminate() -> Bool {
        terminateCalls += 1
        flagAtTerminate = flags.quitChatGPT
        return terminateAnswer
    }

    func waitUntilTerminated(timeout: Duration) async -> Bool {
        waitTimeouts.append(timeout)
        let quit = holdsTheWait ? await withCheckedContinuation { heldWait = $0 } : quitsInTime
        if quit { isRunning = false }
        return quit
    }

    func endWait(quit: Bool) {
        heldWait?.resume(returning: quit)
        heldWait = nil
    }

    func relaunch() async -> Bool {
        relaunchCalls += 1
        flagAtRelaunch.append(flags.quitChatGPT)
        relaunchSawCancel.append(Task.isCancelled)
        let started = relaunchAnswers.isEmpty ? true : relaunchAnswers.removeFirst()
        if started { isRunning = true }
        return started
    }

    func launches() -> AsyncStream<Void> {
        let (stream, continuation) = AsyncStream<Void>.makeStream()
        openStreams.withLock { $0 += 1 }
        continuation.onTermination = { [openStreams] _ in openStreams.withLock { $0 -= 1 } }
        listeners.append(continuation)
        return stream
    }

    /// The person opens ChatGPT. Returns how many watchers heard it.
    func launch() -> Int {
        listeners.filter {
            if case .terminated = $0.yield(()) { return false }
            return true
        }.count
    }
}

/// `dotsync sessions cleanup codex`, by what it answers.
private actor FakeRunner: CleanupRunner {
    enum Cleanup {
        case answers(CleanupReport)
        case fails(CLIError)
        /// Runs until its task is cancelled (SIGTERM), then answers.
        case runsUntilStopped(CleanupReport)
    }

    /// The answers of `blockers()`, in order; the last one repeats.
    private var blockerAnswers: [Result<CleanupBlockers, CLIError>]
    private let behaviour: Cleanup
    private(set) var blockerCalls = 0
    private(set) var cleanupItems: [String]?
    private(set) var cleanupVacuum: Bool?
    /// Whether the cleanup's task was cancelled when it answered.
    private(set) var cleanupSawCancel: Bool?
    /// Yields once the cleanup has begun.
    nonisolated let started: AsyncStream<Void>
    private let startedContinuation: AsyncStream<Void>.Continuation

    init(blockers: [Result<CleanupBlockers, CLIError>], cleanup: Cleanup) {
        blockerAnswers = blockers
        behaviour = cleanup
        (started, startedContinuation) = AsyncStream<Void>.makeStream()
    }

    func blockers() async throws -> CleanupBlockers {
        blockerCalls += 1
        let answer = blockerAnswers.count > 1 ? blockerAnswers.removeFirst() : blockerAnswers[0]
        return try answer.get()
    }

    func cleanup(items: [String], vacuum: Bool, progress: SessionService.Progress?) async throws -> CleanupReport {
        cleanupItems = items
        cleanupVacuum = vacuum
        progress?(1, 2)
        startedContinuation.yield()
        defer { cleanupSawCancel = Task.isCancelled }
        switch behaviour {
        case .answers(let report):
            return report
        case .fails(let error):
            throw error
        case .runsUntilStopped(let report):
            try? await Task.sleep(for: .seconds(60))
            return report
        }
    }
}

/// The injected sleep: it records and returns at once.
@MainActor
private final class Sleeps {
    private(set) var durations: [Duration] = []
    private(set) var sawCancel: [Bool] = []

    func record(_ duration: Duration) {
        durations.append(duration)
        sawCancel.append(Task.isCancelled)
    }
}

@MainActor
private struct Rig {
    let flags = FakeFlags()
    let control: FakeChatGPT
    let runner: FakeRunner
    let sleeps = Sleeps()
    let unit: CleanupUnit

    /// `blockers` are the answers of `--blockers`: the first is step 1's
    /// recheck, the next ones the polls after ChatGPT quit.
    init(running: Bool = true, blockers: [Result<CleanupBlockers, CLIError>] = [.success(chatgptOnly), .success(nothingRuns)],
         cleanup: FakeRunner.Cleanup = .answers(cleaned)) {
        control = FakeChatGPT(running: running, flags: flags)
        runner = FakeRunner(blockers: blockers, cleanup: cleanup)
        let sleeps = sleeps
        unit = CleanupUnit(control: control, runner: runner, flags: flags, sleep: { sleeps.record($0) })
    }

    func run(shownOpenThreads: Int = 0, progress: SessionService.Progress? = nil,
             isPoweringOff: @escaping () -> Bool = { false }) async -> CleanupOutcome {
        await unit.run(items: ["cache_files", "orphan_rollouts"], vacuum: true, shownOpenThreads: shownOpenThreads,
                       progress: progress, isPoweringOff: isPoweringOff)
    }
}

// MARK: The unit

@MainActor
@Test func chatgptIsQuitForTheCleanupAndRelaunched() async {
    let rig = Rig()
    let steps = ProgressLog()
    let outcome = await rig.run(progress: { steps.add($0, $1) })

    #expect(outcome == .finished(cleaned, relaunch: .relaunched))
    #expect(rig.control.terminateCalls == 1)
    #expect(rig.control.waitTimeouts == [.seconds(30)])
    #expect(await rig.runner.blockerCalls == 2)
    #expect(await rig.runner.cleanupItems == ["cache_files", "orphan_rollouts"])
    #expect(await rig.runner.cleanupVacuum == true)
    #expect(steps.all == [[1, 2]])
    #expect(rig.control.relaunchCalls == 1)
    #expect(rig.flags.writes == [true, false])
    #expect(rig.sleeps.durations.isEmpty)
    #expect(rig.control.isRunning)
    #expect(rig.control.openStreams.withLock { $0 } == 0)
}

@MainActor
@Test func chatgptThatDoesNotQuitSkipsCleanupAndRelaunches() async {
    let rig = Rig()
    rig.control.quitsInTime = false
    let outcome = await rig.run()

    #expect(outcome == .chatgptDidNotQuit(["ChatGPT", "codex"], relaunch: .relaunched))
    #expect(await rig.runner.cleanupItems == nil)
    #expect(await rig.runner.blockerCalls == 1)
    #expect(rig.control.relaunchCalls == 1)
    #expect(rig.flags.quitChatGPT == false)
}

@MainActor
@Test func refusedTerminateSkipsCleanupAndRelaunches() async {
    let rig = Rig()
    rig.control.terminateAnswer = false
    let outcome = await rig.run()

    #expect(outcome == .chatgptDidNotQuit(["ChatGPT", "codex"], relaunch: .relaunched))
    #expect(rig.control.waitTimeouts.isEmpty)
    #expect(await rig.runner.cleanupItems == nil)
    #expect(rig.control.relaunchCalls == 1)
}

@MainActor
@Test func childrenThatLeaveWithinFifteenSecondsLetTheCleanupRun() async {
    let rig = Rig(blockers: [
        .success(chatgptOnly), .success(found([appServer])), .success(found([appServer])), .success(nothingRuns),
    ])
    let outcome = await rig.run()

    #expect(outcome == .finished(cleaned, relaunch: .relaunched))
    #expect(await rig.runner.blockerCalls == 4)
    #expect(rig.sleeps.durations == [.seconds(1), .seconds(1)])
}

@MainActor
@Test func aChildLeftAfterFifteenSecondsSkipsCleanupAndRelaunches() async {
    let rig = Rig(blockers: [.success(chatgptOnly), .success(found([appServer, computerUse, appServer]))])
    let outcome = await rig.run()

    #expect(outcome == .chatgptDidNotQuit(["codex", "SkyComputerUseService"], relaunch: .relaunched))
    // Polled at 0, 1, …, 15 s.
    #expect(await rig.runner.blockerCalls == 1 + 16)
    #expect(rig.sleeps.durations == Array(repeating: .seconds(1), count: 15))
    #expect(await rig.runner.cleanupItems == nil)
    #expect(rig.control.relaunchCalls == 1)
}

@MainActor
@Test func refusedCleanupStillRelaunches() async {
    let rig = Rig(cleanup: .fails(codexRunning))
    let outcome = await rig.run()

    #expect(outcome == .failed(codexRunning, relaunch: .relaunched))
    #expect(rig.control.relaunchCalls == 1)
    #expect(rig.flags.quitChatGPT == false)
    #expect(rig.control.openStreams.withLock { $0 } == 0)
}

@MainActor
@Test func relaunchFailureRetriesThenReportsFailed() async {
    let rig = Rig()
    rig.control.relaunchAnswers = Array(repeating: false, count: 13) + [true]
    let outcome = await rig.run()

    #expect(outcome == .finished(cleaned, relaunch: .failed))
    // Tried at 0, 5, …, 60 s.
    #expect(rig.control.relaunchCalls == 13)
    #expect(rig.sleeps.durations == Array(repeating: .seconds(5), count: 12))
    #expect(rig.sleeps.durations.reduce(.zero, +) == .seconds(60))
    #expect(rig.flags.quitChatGPT)
    #expect(CleanupUnit.shouldOfferRelaunch(flags: rig.flags, control: rig.control))
}

@MainActor
@Test func theFlagIsClearedOnlyByARelaunchThatWorked() async {
    let rig = Rig()
    rig.control.relaunchAnswers = [false, false, true]
    let outcome = await rig.run()

    #expect(outcome == .finished(cleaned, relaunch: .relaunched))
    #expect(rig.control.flagAtRelaunch == [true, true, true])
    #expect(rig.flags.writes == [true, false])
    #expect(rig.sleeps.durations == [.seconds(5), .seconds(5)])
}

@MainActor
@Test func persistedFlagIsOfferedNextLaunch() async {
    let rig = Rig()
    rig.control.relaunchAnswers = Array(repeating: false, count: 13)
    _ = await rig.run()

    // The next launch: the same defaults, ChatGPT still off.
    #expect(CleanupUnit.shouldOfferRelaunch(flags: rig.flags, control: FakeChatGPT(running: false, flags: rig.flags)))
    // Opened by hand meanwhile: nothing to offer.
    #expect(!CleanupUnit.shouldOfferRelaunch(flags: rig.flags, control: FakeChatGPT(running: true, flags: rig.flags)))
    let fresh = FakeFlags()
    #expect(!CleanupUnit.shouldOfferRelaunch(flags: fresh, control: FakeChatGPT(running: false, flags: fresh)))

    let relaunched = Rig()
    _ = await relaunched.run()
    #expect(!CleanupUnit.shouldOfferRelaunch(
        flags: relaunched.flags, control: FakeChatGPT(running: false, flags: relaunched.flags)))
}

@MainActor
@Test(.timeLimit(.minutes(1))) func chatgptLaunchedMidRunCancelsTheCleanup() async {
    let rig = Rig(cleanup: .runsUntilStopped(stoppedRun))
    let running = Task { await rig.run() }
    for await _ in rig.runner.started { break }
    #expect(rig.control.launch() == 1)
    let outcome = await running.value

    #expect(outcome == .finished(stoppedRun, relaunch: .relaunched))
    #expect(await rig.runner.cleanupSawCancel == true)
    #expect(rig.control.relaunchCalls == 1)
    #expect(rig.control.openStreams.withLock { $0 } == 0)
}

@MainActor
@Test func aLaunchAfterTheUnitFinishedDoesNothing() async {
    let rig = Rig()
    let outcome = await rig.run()
    #expect(outcome == .finished(cleaned, relaunch: .relaunched))

    #expect(rig.control.openStreams.withLock { $0 } == 0)
    #expect(rig.control.launch() == 0)
    #expect(await rig.runner.cleanupSawCancel == false)
    #expect(rig.control.relaunchCalls == 1)
}

@MainActor
@Test func moreOpenThreadsAsksAgain() async {
    let rig = Rig(blockers: [.success(found([chatgptApp, appServer], openThreads: 3))])
    #expect(await rig.run(shownOpenThreads: 2) == .needsConfirmation(openThreads: 3))
    #expect(rig.control.terminateCalls == 0)
    #expect(rig.flags.writes.isEmpty)
    #expect(await rig.runner.cleanupItems == nil)

    // The same count as the sheet showed: the press was the confirmation.
    let confirmed = Rig(blockers: [.success(found([chatgptApp, appServer], openThreads: 3)), .success(nothingRuns)])
    #expect(await confirmed.run(shownOpenThreads: 3) == .finished(cleaned, relaunch: .relaunched))
}

@MainActor
@Test func newTerminalCodexBlocks() async {
    let rig = Rig(blockers: [.success(found([chatgptApp, terminalCodex, otherProgram], openThreads: 5))])
    // A blocker stops the unit even when the open threads grew too.
    #expect(await rig.run(shownOpenThreads: 0) == .blocked([terminalCodex, otherProgram]))
    #expect(rig.control.terminateCalls == 0)
    #expect(rig.flags.writes.isEmpty)
    #expect(await rig.runner.cleanupItems == nil)
    #expect(rig.control.relaunchCalls == 0)
}

@MainActor
@Test func aFailedDetectionBlocks() async {
    let rig = Rig(blockers: [.success(found([detectionFailed]))])
    #expect(await rig.run() == .blocked([detectionFailed]))
    #expect(rig.control.terminateCalls == 0)
}

@MainActor
@Test func blockersThatFailAtTheRecheckTouchNothing() async {
    let rig = Rig(blockers: [.failure(CLIError(code: "timeout", message: "dotsync가 제때 끝나지 않았어요"))])
    let outcome = await rig.run()

    #expect(outcome == .failed(CLIError(code: "timeout", message: "dotsync가 제때 끝나지 않았어요"), relaunch: .notNeeded))
    #expect(rig.control.terminateCalls == 0)
    #expect(rig.flags.writes.isEmpty)
    #expect(rig.control.relaunchCalls == 0)
}

@MainActor
@Test func blockersThatFailWhileWaitingForChildrenCountAsNotQuit() async {
    let rig = Rig(blockers: [.success(chatgptOnly), .failure(CLIError(code: "failed", message: "boom"))])
    let outcome = await rig.run()

    #expect(outcome == .chatgptDidNotQuit(["ChatGPT", "codex"], relaunch: .relaunched))
    #expect(await rig.runner.cleanupItems == nil)
    #expect(rig.control.relaunchCalls == 1)
}

@MainActor
@Test func chatgptThatIsNotRunningIsNeitherQuitNorRelaunched() async {
    let rig = Rig(running: false, blockers: [.success(nothingRuns)])
    let outcome = await rig.run()

    #expect(outcome == .finished(cleaned, relaunch: .notNeeded))
    #expect(rig.control.terminateCalls == 0)
    #expect(rig.flags.writes.isEmpty)
    #expect(rig.control.relaunchCalls == 0)
    #expect(await rig.runner.blockerCalls == 1)
}

@MainActor
@Test func aCleanupFlagLeftFromBeforeIsNotThisRunsToRelaunch() async {
    // An earlier relaunch failed and ChatGPT stayed off: the next launch's
    // offer owns that flag, and this run, which quit nothing, leaves it.
    let rig = Rig(running: false, blockers: [.success(nothingRuns)])
    rig.flags.set(true, forKey: CleanupUnit.flagKey)
    #expect(await rig.run() == .finished(cleaned, relaunch: .notNeeded))
    #expect(rig.control.relaunchCalls == 0)
    #expect(rig.flags.quitChatGPT)
}

@MainActor
@Test func powerOffRelaunchesNothing() async {
    let rig = Rig()
    let outcome = await rig.run(isPoweringOff: { true })

    #expect(outcome == .finished(cleaned, relaunch: .skippedForPowerOff))
    #expect(rig.control.relaunchCalls == 0)
    #expect(rig.sleeps.durations.isEmpty)
    #expect(rig.flags.quitChatGPT)
    #expect(CleanupUnit.shouldOfferRelaunch(flags: rig.flags, control: rig.control))
}

@MainActor
@Test func powerOffBetweenRetriesStopsRelaunching() async {
    let rig = Rig()
    rig.control.relaunchAnswers = [false, false, false]
    let control = rig.control
    let outcome = await rig.run(isPoweringOff: { control.relaunchCalls >= 2 })

    #expect(outcome == .finished(cleaned, relaunch: .skippedForPowerOff))
    #expect(rig.control.relaunchCalls == 2)
    #expect(rig.sleeps.durations == [.seconds(5), .seconds(5)])
    #expect(rig.flags.quitChatGPT)
}

@MainActor
@Test func flagIsSetBeforeTerminate() async {
    let rig = Rig()
    _ = await rig.run()
    #expect(rig.control.flagAtTerminate == true)
    #expect(rig.flags.writes.first == true)
}

@MainActor
@Test(.timeLimit(.minutes(1))) func cancellingTheRunStillRelaunches() async {
    let rig = Rig(cleanup: .runsUntilStopped(stoppedRun))
    rig.control.relaunchAnswers = [false, true]
    let running = Task { await rig.run() }
    for await _ in rig.runner.started { break }
    running.cancel()
    let outcome = await running.value

    #expect(outcome == .finished(stoppedRun, relaunch: .relaunched))
    #expect(await rig.runner.cleanupSawCancel == true)
    // The relaunch and its retry ran outside the cancelled task.
    #expect(rig.control.relaunchSawCancel == [false, false])
    #expect(rig.sleeps.sawCancel == [false])
    #expect(rig.flags.quitChatGPT == false)
    #expect(rig.control.openStreams.withLock { $0 } == 0)
}

@MainActor
@Test(.timeLimit(.minutes(1))) func cancellingWhileChatGPTQuitsSkipsTheCleanupAndRelaunches() async {
    let rig = Rig()
    rig.control.holdsTheWait = true
    let running = Task { await rig.run() }
    while !rig.control.isWaiting && !Task.isCancelled { await Task.yield() }
    running.cancel()
    rig.control.endWait(quit: true)
    let outcome = await running.value

    #expect(outcome == .failed(stoppedBeforeCleanup, relaunch: .relaunched))
    #expect(await rig.runner.cleanupItems == nil)
    #expect(await rig.runner.blockerCalls == 1)
    #expect(rig.control.relaunchSawCancel == [false])
    #expect(rig.flags.quitChatGPT == false)
}

@MainActor
@Test func cancelledBeforeChatGPTIsTouchedEndsAtTheRecheck() async {
    let rig = Rig()
    // Cancelled before it starts: the task runs on the main actor, which
    // this test holds until it awaits.
    let running = Task { await rig.run() }
    running.cancel()
    #expect(await running.value == .failed(stoppedBeforeCleanup, relaunch: .notNeeded))
    #expect(rig.control.terminateCalls == 0)
    #expect(rig.flags.writes.isEmpty)
}

// MARK: The gate

private func expectGate(_ gate: (allowed: Bool, note: String?), _ allowed: Bool, _ note: String?,
                        sourceLocation: SourceLocation = #_sourceLocation) {
    #expect(gate.allowed == allowed, sourceLocation: sourceLocation)
    #expect(gate.note == note, sourceLocation: sourceLocation)
}

@Test func gateBlocksOnTerminalCodexDetectionFailureAndRefusal() {
    let ready = preview()
    expectGate(CleanupGate.canStart(blockers: chatgptOnly, preview: ready), true, nil)
    expectGate(CleanupGate.canStart(blockers: found([chatgptApp, appServer, computerUse]), preview: ready), true, nil)
    expectGate(CleanupGate.canStart(blockers: nothingRuns, preview: ready), true, nil)

    let terminal = "터미널 Codex가 끝나면 이 버튼이 켜져요"
    let program = "Codex를 쓰는 프로그램이 끝나면 이 버튼이 켜져요"
    expectGate(CleanupGate.canStart(blockers: found([chatgptApp, terminalCodex]), preview: ready), false, terminal)
    expectGate(CleanupGate.canStart(blockers: found([otherProgram]), preview: ready), false, program)
    // A program that is not a terminal Codex is waited for too.
    expectGate(CleanupGate.canStart(blockers: found([terminalCodex, otherProgram]), preview: ready), false, program)

    let undetected = "실행 중인 Codex를 확인하지 못했어요"
    expectGate(CleanupGate.canStart(blockers: found([detectionFailed]), preview: ready), false, undetected)
    expectGate(CleanupGate.canStart(blockers: found([terminalCodex, detectionFailed]), preview: ready), false, undetected)

    let refusal = CLIError(code: "unsupported_codex", message: "this Codex version is not supported yet")
    expectGate(CleanupGate.canStart(blockers: chatgptOnly, preview: preview(refusal: refusal)), false, refusal.message)
}

@Test func gateWaitsForTheDryRun() {
    expectGate(CleanupGate.canStart(blockers: chatgptOnly, preview: nil), false, nil)
    // A blocker's note shows before the dry-run arrives.
    expectGate(CleanupGate.canStart(blockers: found([terminalCodex]), preview: nil), false,
               "터미널 Codex가 끝나면 이 버튼이 켜져요")
}
