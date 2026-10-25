import Foundation

// The Codex cleanup as one unit of work (spec "Cleanup flow: one unit of
// work"): recheck the blockers, quit ChatGPT.app, run `cleanup codex --yes`,
// and relaunch ChatGPT if dotsync quit it, whatever happened in between. The
// app never ends the unit with ChatGPT left quit by dotsync. Pure logic: the
// app's adapter talks to ChatGPT.app, and the waits between tries are an
// injected sleep.

/// ChatGPT.app (bundle id `com.openai.codex`), as the cleanup unit needs it.
@MainActor
public protocol ChatGPTControl {
    var isRunning: Bool { get }
    /// Asks ChatGPT to quit; false when the request is refused.
    func terminate() -> Bool
    /// True once ChatGPT has quit; false at `timeout`.
    func waitUntilTerminated(timeout: Duration) async -> Bool
    /// Opens ChatGPT; false when it did not open.
    func relaunch() async -> Bool
    /// One value each time ChatGPT is launched. It listens from the moment it
    /// returns and keeps every launch until it is read, even one that comes
    /// before anyone iterates it: the unit asks for it before the cleanup
    /// starts and reads it only after. It stops listening when its consumer is
    /// cancelled.
    func launches() -> AsyncStream<Void>
}

/// The `cleanup codex` commands the unit runs. `SessionService` runs them.
public protocol CleanupRunner: Sendable {
    func blockers() async throws -> CleanupBlockers
    /// Cancelling the calling task stops the CLI at its next safe point
    /// (SIGTERM); it then answers with `stopped`.
    func cleanup(items: [String], vacuum: Bool, progress: SessionService.Progress?) async throws -> CleanupReport
}

extension SessionService: CleanupRunner {}

/// Where `dotsyncQuitChatGPT` is kept: UserDefaults' own two calls.
public protocol FlagStore {
    func bool(forKey key: String) -> Bool
    func set(_ value: Bool, forKey key: String)
}

extension UserDefaults: FlagStore {}

/// Step 5: what became of ChatGPT after the unit.
public enum RelaunchResult: Equatable, Sendable {
    /// The flag was not set: dotsync has not left ChatGPT quit.
    case notNeeded
    /// It is open again, and the flag is cleared.
    case relaunched
    /// It did not open within 60 s. The flag stays set; the sheet offers
    /// "ChatGPT 다시 켜기".
    case failed
    /// The Mac is logging out or restarting. The flag stays set, so the next
    /// launch offers the relaunch.
    case skippedForPowerOff
}

public enum CleanupOutcome: Equatable, Sendable {
    /// More ChatGPT sessions are open than the sheet warned about: show the
    /// new count and wait for another press. ChatGPT was not touched.
    case needsConfirmation(openThreads: Int)
    /// Something other than ChatGPT uses ~/.codex, or the check could not
    /// tell. ChatGPT was not touched.
    case blocked([Blocker])
    /// "ChatGPT를 끄지 못했어요": it refused to quit, or it or one of its
    /// processes is still there. The names of what is left. Nothing was
    /// cleaned.
    case chatgptDidNotQuit([String], relaunch: RelaunchResult)
    /// The CLI's answer; `stopped` when a ChatGPT launch or a cancel stopped it.
    case finished(CleanupReport, relaunch: RelaunchResult)
    /// The recheck or the cleanup failed, or the unit was cancelled or the Mac
    /// began to power off before the cleanup started (`cancelled`).
    case failed(CLIError, relaunch: RelaunchResult)
}

@MainActor
public final class CleanupUnit {
    /// The UserDefaults key: dotsync quit ChatGPT and has not reopened it.
    nonisolated public static let flagKey = "dotsyncQuitChatGPT"
    static let quitTimeout: Duration = .seconds(30)
    /// ChatGPT's processes are looked for at 0, 1, …, 15 s after it quit.
    static let childPolls = 16
    static let childPollInterval: Duration = .seconds(1)
    /// Relaunch tries at 0, 5, …, 60 s: a pending Sparkle update may be
    /// installing.
    static let relaunchAttempts = 13
    static let relaunchInterval: Duration = .seconds(5)
    /// The answer for a unit cancelled, or powering off, before its cleanup
    /// began: the one `DotsyncCLI` gives for a command stopped before it
    /// answered.
    static let stoppedBeforeCleanup = CLIError(code: "cancelled", message: "dotsync was stopped")

    private let control: any ChatGPTControl
    private let runner: any CleanupRunner
    private let flags: any FlagStore
    private let sleep: (Duration) async -> Void

    public init(control: any ChatGPTControl, runner: any CleanupRunner, flags: any FlagStore,
                sleep: @escaping (Duration) async -> Void = { try? await Task.sleep(for: $0) }) {
        self.control = control
        self.runner = runner
        self.flags = flags
        self.sleep = sleep
    }

    /// The spec's steps 1–6. `shownOpenThreads` is the count the sheet warned
    /// about. `isPoweringOff` is asked before the cleanup starts and before
    /// each relaunch try. Cancelling the calling task stops the unit like a
    /// ChatGPT launch does, and still relaunches ChatGPT.
    public func run(items: [String], vacuum: Bool, shownOpenThreads: Int, progress: SessionService.Progress? = nil,
                    isPoweringOff: @escaping () -> Bool) async -> CleanupOutcome {
        // 1. Recheck. A blocker wins over a grown count: a second press would
        // only be refused.
        let seen: CleanupBlockers
        do {
            seen = try await runner.blockers()
        } catch {
            return .failed(Self.cliError(error), relaunch: .notNeeded)
        }
        let others = seen.blockers.filter { !$0.isChatGPT }
        if !others.isEmpty { return .blocked(others) }
        if seen.chatgptOpenThreads > shownOpenThreads {
            return .needsConfirmation(openThreads: seen.chatgptOpenThreads)
        }
        guard !Task.isCancelled else { return .failed(Self.stoppedBeforeCleanup, relaunch: .notNeeded) }

        // 2. The flag is saved before terminate(): if dotsync dies after it,
        // the next launch still offers the relaunch.
        var left: [String]?
        if control.isRunning {
            flags.set(true, forKey: Self.flagKey)
            left = await quit(seen)
        }
        let ending: Ending
        if Task.isCancelled || isPoweringOff() {
            // A cancel or a logout during the quit phase starts no cleanup.
            // Asked first: a wait cut short is not a ChatGPT that would not quit.
            ending = .failed(Self.stoppedBeforeCleanup)
        } else if let left {
            ending = .didNotQuit(left)  // 3.
        } else {
            ending = await clean(items: items, vacuum: vacuum, progress: progress)  // 4.
        }
        // 5., whatever happened, and also for a flag an earlier run left.
        let reopened = flags.bool(forKey: Self.flagKey) ? await relaunch(unlessPoweringOff: isPoweringOff) : .notNeeded
        return ending.outcome(relaunch: reopened)
    }

    /// On launch: dotsync quit ChatGPT and it is still off, so the app asks
    /// "dotsync가 끈 ChatGPT를 다시 켤까요?".
    public static func shouldOfferRelaunch(flags: any FlagStore, control: any ChatGPTControl) -> Bool {
        flags.bool(forKey: flagKey) && !control.isRunning
    }

    /// Steps 2–4, before the relaunch.
    private enum Ending {
        case didNotQuit([String])
        case finished(CleanupReport)
        case failed(CLIError)

        func outcome(relaunch: RelaunchResult) -> CleanupOutcome {
            switch self {
            case .didNotQuit(let names): return .chatgptDidNotQuit(names, relaunch: relaunch)
            case .finished(let report): return .finished(report, relaunch: relaunch)
            case .failed(let error): return .failed(error, relaunch: relaunch)
            }
        }
    }

    /// Step 2: nil once ChatGPT and its processes are gone; otherwise the
    /// names of what is left. The processes are the ones that were ChatGPT's
    /// at the recheck, whatever the CLI calls them now (with ChatGPT gone, a
    /// child that lingers is reparented and no longer reads as ChatGPT's),
    /// and any `chatgpt_child` that started since.
    private func quit(_ seen: CleanupBlockers) async -> [String]? {
        let chatgpt = seen.blockers.filter(\.isChatGPT)
        let quitting = Set(chatgpt.map(\.pid))
        var left = Self.names(chatgpt)
        guard control.terminate() else { return left }
        guard await control.waitUntilTerminated(timeout: Self.quitTimeout) else { return left }
        for poll in 1...Self.childPolls {
            if poll > 1 { await sleep(Self.childPollInterval) }
            if Task.isCancelled { return left }
            // A recheck that fails counts as a process left: not knowing is
            // never "gone".
            guard let now = try? await runner.blockers() else { return left }
            let remaining = now.blockers.filter { quitting.contains($0.pid) || $0.isChatGPTChild }
            if remaining.isEmpty { return nil }
            left = Self.names(remaining)
        }
        return left
    }

    /// Step 4. ChatGPT launched meanwhile stops the cleanup at its next safe
    /// point (SIGTERM), as cancelling the caller does; the CLI then answers
    /// `stopped`.
    private func clean(items: [String], vacuum: Bool, progress: SessionService.Progress?) async -> Ending {
        let runner = runner
        let launches = control.launches()  // listening before the cleanup starts
        let cleanup = Task { () -> Ending in
            do {
                return .finished(try await runner.cleanup(items: items, vacuum: vacuum, progress: progress))
            } catch {
                return .failed(Self.cliError(error))
            }
        }
        let watcher = Task {
            for await _ in launches { cleanup.cancel() }
        }
        let ending = await withTaskCancellationHandler {
            await cleanup.value
        } onCancel: {
            cleanup.cancel()
        }
        // Stop listening, on every path: nothing is left watching ChatGPT.
        watcher.cancel()
        await watcher.value
        return ending
    }

    /// Step 5, on a task of its own: a cancelled caller must not skip it.
    private func relaunch(unlessPoweringOff isPoweringOff: @escaping () -> Bool) async -> RelaunchResult {
        await Task { await self.relaunchUntilItOpens(isPoweringOff) }.value
    }

    private func relaunchUntilItOpens(_ isPoweringOff: () -> Bool) async -> RelaunchResult {
        for attempt in 1...Self.relaunchAttempts {
            if attempt > 1 { await sleep(Self.relaunchInterval) }
            if isPoweringOff() { return .skippedForPowerOff }
            if await control.relaunch() {
                flags.set(false, forKey: Self.flagKey)
                return .relaunched
            }
        }
        return .failed
    }

    private static func cliError(_ error: Error) -> CLIError {
        error as? CLIError ?? CLIError(code: "failed", message: error.localizedDescription)
    }

    /// Each process name once, in order.
    private static func names(_ blockers: [Blocker]) -> [String] {
        var seen = Set<String>()
        return blockers.map(\.name).filter { seen.insert($0).inserted }
    }
}

/// Whether "끄고 정리하기" may be pressed (spec "the blocker gate"), and the
/// line that says why not.
public enum CleanupGate {
    public static func canStart(blockers: CleanupBlockers, preview: CleanupPreview?) -> (allowed: Bool, note: String?) {
        let others = blockers.blockers.filter { !$0.isChatGPT }
        if others.contains(where: { $0.kind == "detection_failed" }) {
            return (false, "실행 중인 Codex를 확인하지 못했어요")
        }
        if !others.isEmpty {
            let terminalOnly = others.allSatisfy { $0.kind == "terminal_codex" }
            return (false, terminalOnly ? "터미널 Codex가 끝나면 이 버튼이 켜져요" : "Codex를 쓰는 프로그램이 끝나면 이 버튼이 켜져요")
        }
        // The dry-run has not arrived yet.
        guard let preview else { return (false, nil) }
        if let refusal = preview.refusal { return (false, refusal.message) }
        return (true, nil)
    }
}

extension Blocker {
    /// ChatGPT.app or a process it started: the unit quits these. Every other
    /// kind, `detection_failed` too, blocks the cleanup.
    var isChatGPT: Bool { kind == "chatgpt_app" || isChatGPTChild }

    var isChatGPTChild: Bool { kind == "chatgpt_child" }
}
