import Foundation

/// What quitting does while dotsync works (spec "Quitting while sessions work
/// runs"). Long work — a session delete or recover, the Codex cleanup unit, a
/// sync apply — must not be cut off halfway: the window stays and says the app
/// quits when it ends. Other work is waited for unseen, 30 s at most. A logout
/// or restart never waits on the window: the long runs get SIGTERM and stop
/// at their next safe point, 20 s at most. Pure logic: the app delegate acts
/// on it.
public enum QuitRules {
    /// The unseen wait for short work: a refresh, a switch, a widget button, a list.
    public static let hiddenWait: Duration = .seconds(30)
    /// How long a logout or restart waits for the long runs to stop.
    public static let powerOffStop: Duration = .seconds(20)

    /// The window's line while long work runs, or nil. A delete, recover or
    /// cleanup is 정리; an apply is 작업, also when both run, since the app
    /// then quits only after the apply too.
    public static func longWork(sessionWork: Bool, applying: Bool) -> String? {
        if applying { return "작업이 끝나면 꺼져요" }
        return sessionWork ? "정리가 끝나면 꺼져요" : nil
    }

    /// The quit Apple event (`kAEQuitApplication`) was sent for a logout,
    /// restart or shutdown, by its `kAEQuitReason`. The app asks this too,
    /// in case the quit comes before `willPowerOffNotification`. A quit with
    /// no reason is the person's; "quit all" is an installer's.
    public static func isPowerOff(eventID: UInt32?, quitReason: UInt32?) -> Bool {
        guard eventID == fourCharCode("quit"), let quitReason else { return false }
        return powerOffReasons.contains(quitReason)
    }

    /// AERegistry.h's kAELogOut, kAEReallyLogOut, kAEShowRestartDialog,
    /// kAEShowShutdownDialog, kAERestart and kAEShutDown, as their codes:
    /// DotsyncKit does not import Carbon.
    static let powerOffReasons = Set(["logo", "rlgo", "rrst", "rsdn", "rest", "shut"].map(fourCharCode))

    private static func fourCharCode(_ code: String) -> UInt32 {
        code.utf8.reduce(0) { $0 << 8 | UInt32($1) }
    }

    /// `applicationShouldTerminate`'s answer. `quitBy` is the end of an unseen
    /// wait already running.
    public static func reply(longWork: String?, isWorking: Bool, quitBy: ContinuousClock.Instant?,
                             now: ContinuousClock.Instant, poweringOff: Bool) -> QuitDecision {
        if poweringOff { return .now }
        if longWork != nil { return .waitVisible }
        if let quitBy { return isWorking && now < quitBy ? .alreadyWaiting : .now }
        return isWorking ? .waitHidden(hiddenWait) : .now
    }

    /// The window's bar while quitting waits: the long work's line, then each
    /// session run's progress ("지우는 중… 3/10"), the label alone before the
    /// CLI has counted.
    public static func notice(_ longWork: String, runs: [(label: String, done: Int, total: Int)]) -> String {
        let progress = runs.map { run in
            run.total > 0 ? "\(run.label) \(SessionText.progress(done: run.done, total: run.total))" : run.label
        }
        return ([longWork] + progress).joined(separator: " · ")
    }
}

public enum QuitDecision: Equatable, Sendable {
    /// Quit (`.terminateNow`).
    case now
    /// Cancel this quit, keep the window with the line, and quit when the
    /// long work ends.
    case waitVisible
    /// Cancel this quit, hide the window, and quit when the work ends or the
    /// time is up.
    case waitHidden(Duration)
    /// Cancel this quit: an unseen wait already runs and quits later.
    case alreadyWaiting
}
