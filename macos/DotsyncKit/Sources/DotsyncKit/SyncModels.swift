import Foundation

/// `dotsync status --json`: every supported app, tracked or not.
public struct StatusReport: Codable, Equatable, Sendable {
    public var dir: String
    public var checkedAt: Date
    public var apps: [AppSyncStatus]
}

/// One app in the status report. An untracked app has only its name,
/// description and `tracked == false`.
public struct AppSyncStatus: Codable, Equatable, Sendable, Identifiable {
    public var name: String
    public var description: String
    public var tracked: Bool
    /// "clean", "dirty", "missing" or "unknown".
    public var state: String?
    public var details: String?
    /// For a dirty app: "local-newer", "folder-newer", "diverged" or "".
    public var direction: String?
    /// Why an app is unknown, e.g. "btt_permission".
    public var reason: String?
    public var changes: [SyncChange]?
    /// The app's [options.<app>] table in dotsync.toml.
    public var options: AppOptions?

    public var id: String { name }
}

public struct AppOptions: Codable, Equatable, Sendable {
    /// BetterTouchTool's preset names.
    public var presets: [String]?
}

public struct SyncChange: Codable, Equatable, Sendable {
    public var label: String
    /// "create", "update", "remove" or "unchanged".
    public var kind: String
    public var details: String
    /// Only in a preview.
    public var diff: String?
}

/// `push|pull … --dry-run --json`.
public struct SyncPreview: Codable, Equatable, Sendable {
    public var dir: String
    /// "from" (push) or "to" (pull).
    public var direction: String
    public var plans: [SyncPlan]
}

public struct SyncPlan: Codable, Equatable, Sendable, Identifiable {
    public var app: String
    public var description: String
    public var changes: [SyncChange]
    /// Claude pull only: the plugins `claude plugin` installs and disables.
    public var pluginActions: PluginActions?

    public var id: String { app }
}

public struct PluginActions: Codable, Equatable, Sendable {
    public var install: [String]
    public var disable: [String]
}

/// `push|pull … --yes --json`.
public struct ApplyReport: Codable, Equatable, Sendable {
    public var dir: String
    public var direction: String
    public var results: [ApplyResult]
}

public struct ApplyResult: Codable, Equatable, Sendable {
    public var app: String
    /// "changed", "unchanged" or "failed".
    public var outcome: String
    public var error: String?
    public var warnings: [String]
}

/// `config dir --json`.
public struct ConfigDirResult: Codable, Equatable, Sendable {
    public var dir: String
    public var rc: RcResult
}

/// What `config dir` did to the shell rc's DOTSYNC_DIR line: "added",
/// "updated" or "already_set".
public struct RcResult: Codable, Equatable, Sendable {
    public var action: String
    public var path: String
}

/// `config apps --json`.
public struct ConfigApps: Codable, Equatable, Sendable {
    public var dir: String
    public var apps: [String]
}

public enum SyncDirection: String, Sendable {
    case pull
    case push
}
