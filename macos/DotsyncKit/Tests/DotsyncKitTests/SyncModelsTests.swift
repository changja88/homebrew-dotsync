import Foundation
import Testing
@testable import DotsyncKit

// Shapes of `dotsync status/push/pull/config … --json` (lib/dotsync/cli_json.py).
let sampleStatus = """
{"dir": "/Users/x/Desktop/dotsync_config", "dir_source": "rc", "checked_at": "2026-10-25T03:04:05Z", "apps": [
 {"name": "claude", "description": "Claude Code (settings + plugins + MCP servers + global rules)", "tracked": true,
  "state": "dirty", "details": "1 file differs", "direction": "local-newer", "reason": "",
  "changes": [{"label": "settings.json", "kind": "update", "details": "+2 −1", "file_changes": [], "diffable": true}]},
 {"name": "bettertouchtool", "description": "BetterTouchTool presets (.bttpreset, names configurable)", "tracked": true,
  "state": "unknown", "details": "BetterTouchTool needs Automation permission", "direction": "", "reason": "btt_permission",
  "changes": [], "options": {"presets": ["Master_bt"]}},
 {"name": "herdr", "description": "Herdr terminal workspace config (config.toml)", "tracked": false}
]}
"""

let samplePreview = """
{"dir": "/x", "dir_source": "env", "direction": "to", "plans": [
 {"app": "claude", "description": "Claude Code", "changes": [
   {"label": "plugins", "kind": "update", "details": "", "file_changes": ["~ a.json", "+ b.json"], "diffable": true,
    "diff": "◦ a.json\\n-old\\n+new"}],
  "plugin_actions": {"install": ["a@m", "b@m"], "disable": ["c@m"]}},
 {"app": "zsh", "description": "Zsh shell config (~/.zshrc)", "changes": [
   {"label": ".zshrc", "kind": "unchanged", "details": "", "file_changes": [], "diffable": true}]}
]}
"""

let sampleApply = """
{"dir": "/x", "dir_source": "env", "direction": "from", "results": [
 {"app": "zsh", "outcome": "changed", "warnings": []},
 {"app": "codex", "outcome": "failed", "error": "codex plugin list timed out after 120 s", "warnings": ["skipped x"]}
]}
"""

let sampleConfigDir = """
{"dir": "/Users/x/new", "dir_source": "rc", "rc": {"action": "updated", "path": "/Users/x/.zshrc"}}
"""

let sampleConfigApps = """
{"dir": "/x", "dir_source": "env", "apps": ["zsh", "claude"]}
"""

private func decoded<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try UsageJSON.decoder().decode(type, from: Data(json.utf8))
}

@Test func statusDecodesTrackedAndUntrackedApps() throws {
    let report = try decoded(StatusReport.self, sampleStatus)
    #expect(report.dirSource == "rc")
    #expect(report.checkedAt == Date(timeIntervalSince1970: 1_792_897_445))
    #expect(report.apps.map(\.name) == ["claude", "bettertouchtool", "herdr"])
    let claude = report.apps[0]
    #expect(claude.state == "dirty")
    #expect(claude.direction == "local-newer")
    #expect(claude.details == "1 file differs")
    #expect(claude.changes?.first?.label == "settings.json")
    #expect(claude.changes?.first?.diff == nil)
    #expect(report.apps[1].reason == "btt_permission")
    #expect(report.apps[1].options?.presets == ["Master_bt"])
    #expect(claude.options == nil)
    let herdr = report.apps[2]
    #expect(!herdr.tracked)
    #expect(herdr.state == nil && herdr.changes == nil)
}

@Test func previewDecodesPlansWithDiffsAndPluginActions() throws {
    let preview = try decoded(SyncPreview.self, samplePreview)
    #expect(preview.direction == "to")
    #expect(preview.plans.map(\.app) == ["claude", "zsh"])
    let change = preview.plans[0].changes[0]
    #expect(change.fileChanges == ["~ a.json", "+ b.json"])
    #expect(change.diff == "◦ a.json\n-old\n+new")
    #expect(preview.plans[0].pluginActions == PluginActions(install: ["a@m", "b@m"], disable: ["c@m"]))
    #expect(preview.plans[1].pluginActions == nil)
}

@Test func applyDecodesEachAppsOutcome() throws {
    let report = try decoded(ApplyReport.self, sampleApply)
    #expect(report.results.map(\.outcome) == ["changed", "failed"])
    #expect(report.results[0].error == nil)
    #expect(report.results[1].error == "codex plugin list timed out after 120 s")
    #expect(report.results[1].warnings == ["skipped x"])
}

@Test func configAnswersDecode() throws {
    let dir = try decoded(ConfigDirResult.self, sampleConfigDir)
    #expect(dir.rc == RcResult(action: "updated", path: "/Users/x/.zshrc"))
    let missing = try decoded(ConfigDirResult.self, #"{"dir": "/n", "dir_source": "", "rc": {"action": "rc_missing", "path": null}}"#)
    #expect(missing.rc.path == nil)
    #expect(try decoded(ConfigApps.self, sampleConfigApps).apps == ["zsh", "claude"])
}

@Test func syncServiceSendsTheCommandsTheCLIExpects() async throws {
    let files = try FakeDotsync(script: "")
    let status = try files.file("status.json", sampleStatus)
    let preview = try files.file("preview.json", samplePreview)
    let apply = try files.file("apply.json", sampleApply)
    let dir = try files.file("dir.json", sampleConfigDir)
    let apps = try files.file("apps.json", sampleConfigApps)
    let fake = try FakeDotsync(script: """
    case "$1 $2 $3" in
      status*) cat '\(status)' ;;
      *--dry-run*) cat '\(preview)' ;;
      push*|pull*) cat '\(apply)' ;;
      "config dir"*) cat '\(dir)' ;;
      "config apps"*) cat '\(apps)' ;;
    esac
    """)
    let service = SyncService(cli: fake.cli)
    _ = try await service.status()
    _ = try await service.preview(.pull, app: "claude")
    _ = try await service.preview(.push, app: nil)
    _ = try await service.apply(.pull, app: nil)
    _ = try await service.apply(.push, app: "zsh")
    _ = try await service.setDir("-odd folder")
    _ = try await service.setApps(["zsh", "claude"])
    _ = try await service.setApps([])
    #expect(fake.calls() == [
        ["status", "--json"],
        ["pull", "claude", "--dry-run", "--json"],
        ["push", "--all", "--dry-run", "--json"],
        ["pull", "--all", "--yes", "--json"],
        ["push", "zsh", "--yes", "--json"],
        ["config", "dir", "--json", "--", "-odd folder"],
        ["config", "apps", "zsh,claude", "--json"],
        ["config", "apps", "", "--json"],
    ])
}

@Test func syncServicePassesCLIErrorsThrough() async throws {
    let fake = try FakeDotsync(script: """
    echo '{"error": {"code": "busy", "message": "codex is busy"}}'
    exit 1
    """)
    await #expect(throws: CLIError(code: "busy", message: "codex is busy")) {
        _ = try await SyncService(cli: fake.cli).status()
    }
}
