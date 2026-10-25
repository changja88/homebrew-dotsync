import Foundation

/// What the settings screen runs: `dotsync status`, `push`, `pull` and
/// `config` with `--json`. Failures throw `CLIError`.
public struct SyncService: Sendable {
    public let cli: DotsyncCLI

    public init(cli: DotsyncCLI) {
        self.cli = cli
    }

    /// Homebrew's dotsync; nil when it is missing.
    public static func standard() -> SyncService? {
        DotsyncCLI.locate().map { SyncService(cli: DotsyncCLI(executable: $0)) }
    }

    /// Status and previews only read, so leaving the screen may stop them.
    public func status() async throws -> StatusReport {
        try cli.decode(StatusReport.self,
                       try await cli.runJSON(["status", "--json"], timeout: .seconds(300), stopsOnCancel: true))
    }

    public func preview(_ direction: SyncDirection, app: String?) async throws -> SyncPreview {
        let arguments = [direction.rawValue, app ?? "--all", "--dry-run", "--json"]
        return try cli.decode(SyncPreview.self,
                              try await cli.runJSON(arguments, timeout: .seconds(300), stopsOnCancel: true))
    }

    public func apply(_ direction: SyncDirection, app: String?) async throws -> ApplyReport {
        let arguments = [direction.rawValue, app ?? "--all", "--yes", "--json"]
        return try cli.decode(ApplyReport.self, try await cli.runJSON(arguments, timeout: .seconds(600)))
    }

    public func setDir(_ path: String) async throws -> ConfigDirResult {
        try cli.decode(ConfigDirResult.self,
                       try await cli.runJSON(["config", "dir", "--json", "--", path], timeout: .seconds(30)))
    }

    public func setApps(_ names: [String]) async throws -> ConfigApps {
        let arguments = ["config", "apps", names.joined(separator: ","), "--json"]
        return try cli.decode(ConfigApps.self, try await cli.runJSON(arguments, timeout: .seconds(30)))
    }
}
