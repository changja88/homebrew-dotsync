import Foundation

// Shapes of `dotsync sessions … --json` (lib/dotsync/cli_sessions.py and
// lib/dotsync/sessions/). Decoded with `UsageJSON.decoder()`.
//
// The CLI's vocabularies (state, reason, outcome, kind, stoppedBecause,
// placeReason) are Strings, not enums: a value the app does not know yet must
// still decode. Keys the app does not read are ignored.

public enum SessionTool: String, Decodable, Sendable, Equatable, CaseIterable {
    case claude
    case codex
}

public struct SessionList: Decodable, Sendable, Equatable {
    public var tool: SessionTool
    public var checkedAt: Date
    public var totals: SessionTotals
    public var groups: [SessionGroup]
    /// Why `claude agents` could not say what runs (Claude only).
    public var agentsError: String?
}

public struct SessionTotals: Decodable, Sendable, Equatable {
    public var sessions: Int
    public var bytes: Int64
    public var running: Int
    /// The Codex history database's size (Codex only).
    public var historyDbBytes: Int64?
}

public struct SessionGroup: Decodable, Sendable, Equatable, Identifiable {
    public var key: String
    public var name: String
    public var folders: Int
    public var foldersDeleted: Int
    public var sessions: Int
    public var bytes: Int64
    public var running: Int
    public var items: [SessionItem]

    public var id: String { key }
}

public struct SessionItem: Decodable, Sendable, Equatable, Identifiable {
    public var id: String
    public var title: String
    public var folder: String
    public var cwd: String
    public var lastActivity: Date
    public var bytes: Int64
    /// `idle`, `running`, `unknown`, …
    public var state: String
    public var reason: String?
    public var placeReason: String?
    /// Claude: subagent transcripts.
    public var subagents: Int?
    /// Codex: spawned threads below this one.
    public var children: Int?
    public var archived: Bool?
}

/// The answer of `delete` and `recover`.
public struct DeleteReport: Decodable, Sendable, Equatable {
    public var tool: SessionTool
    public var results: [DeleteResult]
    /// SIGTERM stopped the run before every id was handled.
    public var stopped: Bool
}

public struct DeleteResult: Decodable, Sendable, Equatable {
    public var id: String
    /// `deleted`, `skipped`, `partial`, …
    public var outcome: String
    public var reason: String?
    public var kept: [String]?
    public var failed: [FailedPath]?
    public var freedBytes: Int64?
    public var children: Int?
    /// Codex partial: the parts that are still there.
    public var remaining: [String]?
    /// Codex partial: the parts dotsync could not check afterwards.
    public var unchecked: [String]?
    /// Codex refused: the first line of what `codex delete` said.
    public var message: String?
}

public struct FailedPath: Decodable, Sendable, Equatable {
    public var path: String
    public var error: String
}

/// The answer of `cleanup codex --blockers`.
public struct CleanupBlockers: Decodable, Sendable, Equatable {
    public var blockers: [Blocker]
    public var chatgptHelpers: [Blocker]
    public var chatgptOpenThreads: Int
}

public struct Blocker: Decodable, Sendable, Equatable {
    public var pid: Int
    public var kind: String
    public var name: String
    /// Seconds the process has been running.
    public var elapsed: Int
}

/// The answer of `cleanup codex --dry-run`: the blocker fields, then what a
/// cleanup would remove. A refusal comes with no items.
public struct CleanupPreview: Decodable, Sendable, Equatable {
    public var blockers: [Blocker]
    public var chatgptHelpers: [Blocker]
    public var chatgptOpenThreads: Int
    public var items: [CleanupItem]
    public var historyDbBytes: Int64
    public var vacuumAvailable: Bool
    public var refusal: CLIError?
}

public struct CleanupItem: Decodable, Sendable, Equatable, Identifiable {
    public var id: String
    public var label: String
    public var count: Int
    public var lines: Int?
    /// The CLI does not report sizes per item.
    public var bytes: Int64?
    public var checked: Bool
}

/// The answer of `cleanup codex --yes`. The items always end with
/// `cache_files`. `stopped` is true only after SIGTERM; a Codex that started
/// during the run shows as items `skipped` with reason `codex_started`.
public struct CleanupReport: Decodable, Sendable, Equatable {
    public var items: [CleanupItemResult]
    public var vacuum: VacuumResult?
    public var stopped: Bool
}

public struct CleanupItemResult: Decodable, Sendable, Equatable {
    public var id: String
    /// `done`, `skipped`, `failed`, …
    public var outcome: String
    public var count: Int
    public var lines: Int?
    public var reason: String?
    /// A failed item: what went wrong.
    public var message: String?
}

public struct VacuumResult: Decodable, Sendable, Equatable {
    public var bytesBefore: Int64
    public var bytesAfter: Int64
    /// `freelist_empty`, `busy`, `stopped`, `error`, …
    public var stoppedBecause: String
    /// With `error`: what went wrong.
    public var message: String?
}

/// One `~/Library/Logs/dotsync/sessions-*.json` (lib/dotsync/sessions/log.py).
public struct SessionLogEntry: Decodable, Sendable, Equatable {
    public enum Result: Sendable, Equatable {
        case delete(DeleteReport)
        case cleanup(CleanupReport)
    }

    /// `delete`, `recover` or `cleanup`.
    public var command: String
    public var tool: String
    public var startedAt: Date
    public var finishedAt: Date
    /// nil when the run left no report: refused or busy runs log an error
    /// instead of `results`/`items`.
    public var result: Result?

    private enum CodingKeys: String, CodingKey {
        case command, tool, startedAt, finishedAt, result
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        command = try container.decode(String.self, forKey: .command)
        tool = try container.decode(String.self, forKey: .tool)
        startedAt = try container.decode(Date.self, forKey: .startedAt)
        finishedAt = try container.decode(Date.self, forKey: .finishedAt)
        if command == "cleanup" {
            result = (try? container.decode(CleanupReport.self, forKey: .result)).map(Result.cleanup)
        } else {
            result = (try? container.decode(DeleteReport.self, forKey: .result)).map(Result.delete)
        }
    }
}
