import Foundation

/// What the sessions screen runs: `dotsync sessions …` with `--json`.
/// Failures throw `CLIError`.
public struct SessionService: Sendable {
    /// done, total, and the phase word of a cleanup line (nil for delete and recover).
    public typealias Progress = @Sendable (Int, Int, String?) -> Void

    public let cli: DotsyncCLI

    public init(cli: DotsyncCLI) {
        self.cli = cli
    }

    /// Homebrew's dotsync; nil when it is missing.
    public static func standard() -> SessionService? {
        DotsyncCLI.locate().map { SessionService(cli: DotsyncCLI(executable: $0)) }
    }

    /// A list only reads, so leaving the screen may stop it.
    public func list(_ tool: SessionTool) async throws -> SessionList {
        try cli.decode(SessionList.self, try await cli.runJSON(
            ["sessions", "list", tool.rawValue, "--json"], timeout: .seconds(300), stopsOnCancel: true))
    }

    /// Cancelling the calling task stops the CLI at its next safe point; it
    /// then answers with what it did (`stopped`), and that answer is returned.
    public func delete(_ tool: SessionTool, ids: [String], progress: Progress? = nil) async throws -> DeleteReport {
        try cli.decode(DeleteReport.self, try await cli.runJSON(
            ["sessions", "delete", tool.rawValue, "--json", "--"] + ids,
            timeout: .seconds(120 + 200 * ids.count), stopsOnCancel: true, longRun: true, progress: progress))
    }

    /// Finishes (deletes what is left) or undoes (renames the transcripts
    /// back) a delete that stopped halfway. Only finishing stops on cancel:
    /// the CLI has no safe point in the middle of undoing.
    public func recover(_ id: String, finish: Bool, progress: Progress? = nil) async throws -> DeleteReport {
        try cli.decode(DeleteReport.self, try await cli.runJSON(
            ["sessions", "recover", "claude", "--json", finish ? "--finish" : "--restore", "--", id],
            timeout: .seconds(300), stopsOnCancel: finish, longRun: true, progress: progress))
    }

    public func blockers() async throws -> CleanupBlockers {
        try cli.decode(CleanupBlockers.self, try await cli.runJSON(
            ["sessions", "cleanup", "codex", "--blockers", "--json"], timeout: .seconds(60)))
    }

    /// A dry run only reads, so leaving the screen may stop it.
    public func cleanupPreview(progress: Progress? = nil) async throws -> CleanupPreview {
        try cli.decode(CleanupPreview.self, try await cli.runJSON(
            ["sessions", "cleanup", "codex", "--dry-run", "--json"],
            timeout: .seconds(900), stopsOnCancel: true, progress: progress))
    }

    public func cleanup(items: [String], vacuum: Bool, progress: Progress? = nil) async throws -> CleanupReport {
        let arguments = ["sessions", "cleanup", "codex", "--yes", "--json", "--items", items.joined(separator: ",")]
        return try cli.decode(CleanupReport.self, try await cli.runJSON(
            arguments + (vacuum ? ["--vacuum"] : []),
            timeout: .seconds(3600), stopsOnCancel: true, longRun: true, progress: progress))
    }
}

/// The result logs of delete, recover and cleanup runs.
public enum SessionLog {
    /// The newest `sessions-*.json` in `directory` that started at or after
    /// `since`, or nil. A file that cannot be read is skipped.
    public static func latest(since: Date, in directory: URL) -> SessionLogEntry? {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names
            .filter { $0.hasPrefix("sessions-") && $0.hasSuffix(".json") }
            .compactMap { name -> SessionLogEntry? in
                guard let data = try? Data(contentsOf: directory.appendingPathComponent(name)) else { return nil }
                return try? UsageJSON.decoder().decode(SessionLogEntry.self, from: data)
            }
            .filter { $0.startedAt >= since }
            .max { $0.startedAt < $1.startedAt }
    }
}
