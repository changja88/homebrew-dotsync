import Foundation
import Testing
@testable import DotsyncKit

private func app(_ name: String = "zsh", tracked: Bool = true, state: String?, direction: String? = "",
                 reason: String? = "") -> AppSyncStatus {
    AppSyncStatus(name: name, description: "", tracked: tracked, state: state, details: "",
                  direction: direction, reason: reason, changes: [], options: nil)
}

private func report(_ apps: [AppSyncStatus]) -> StatusReport {
    StatusReport(dir: "/x", checkedAt: Date(timeIntervalSince1970: 0), apps: apps)
}

@Test func aCleanAppIsTheSame() {
    #expect(SettingsText.stateLabel(app(state: "clean")) == "같음")
}

@Test func aDirtyAppNewerHereSaysThisMacIsNewer() {
    #expect(SettingsText.stateLabel(app(state: "dirty", direction: "local-newer")) == "이 맥이 최신")
}

@Test func aDirtyAppNewerInTheFolderSaysTheFolderIsNewer() {
    #expect(SettingsText.stateLabel(app(state: "dirty", direction: "folder-newer")) == "폴더가 최신")
}

@Test func aDivergedAppIsDifferent() {
    #expect(SettingsText.stateLabel(app(state: "dirty", direction: "diverged")) == "다름")
}

@Test func aMissingAppIsDifferent() {
    #expect(SettingsText.stateLabel(app(state: "missing", direction: "")) == "다름")
}

@Test func anUnknownAppCouldNotBeChecked() {
    #expect(SettingsText.stateLabel(app(state: "unknown")) == "확인 못 함")
}

@Test func anUntrackedAppHasNoState() {
    #expect(SettingsText.stateLabel(app(tracked: false, state: nil, direction: nil, reason: nil)) == "—")
}

@Test func theBadgeCountsTrackedAppsThatDiffer() {
    let apps = [
        app(state: "dirty", direction: "local-newer"),
        app(state: "missing"),
        app(state: "clean"),
        app(state: "unknown"),
        app(tracked: false, state: nil),
    ]
    #expect(SettingsText.badgeCount(report(apps)) == 2)
    #expect(SettingsText.banner(report(apps)) == "2개 앱이 폴더와 달라요")
}

@Test func noBannerWhenEveryAppIsTheSame() {
    #expect(SettingsText.banner(report([app(state: "clean")])) == nil)
    #expect(SettingsText.badgeCount(report([app(state: "clean")])) == 0)
}

@Test func bttNotesSayWhyThePresetWasNotCompared() {
    #expect(SettingsText.bttNote(reason: "btt_permission") == "자동화 권한이 필요해요")
    #expect(SettingsText.bttNote(reason: "btt_not_running") == "BTT가 켜져 있어야 비교해요")
    #expect(SettingsText.bttNote(reason: "timeout") == "BTT가 제때 답하지 않았어요")
    #expect(SettingsText.bttNote(reason: "error") == nil)
    #expect(SettingsText.bttNote(reason: "") == nil)
    #expect(SettingsText.bttNote(reason: nil) == nil)
}

@Test func pluginActionsCountInstallsAndDisables() {
    #expect(SettingsText.pluginActionsText(PluginActions(install: ["a", "b"], disable: ["c"]))
        == "플러그인 2개 설치 · 1개 끄기")
}

@Test func pluginActionsDropAnEmptyHalf() {
    #expect(SettingsText.pluginActionsText(PluginActions(install: ["a"], disable: [])) == "플러그인 1개 설치")
    #expect(SettingsText.pluginActionsText(PluginActions(install: [], disable: ["c"])) == "플러그인 1개 끄기")
}

@Test func noPluginActionsHasNoText() {
    #expect(SettingsText.pluginActionsText(PluginActions(install: [], disable: [])) == nil)
}

@Test func checkedAgoSaysWhenStatusRan() {
    let now = Date(timeIntervalSince1970: 10_000)
    #expect(SettingsText.checkedAgo(now.addingTimeInterval(-30), now: now) == "방금 확인")
    #expect(SettingsText.checkedAgo(now.addingTimeInterval(-180), now: now) == "3분 전 확인")
}

@Test func sheetTitlesNameTheApp() {
    #expect(SettingsText.pullTitle(appLabel: "Claude Code") == "Claude Code 설정을 이 맥으로 가져올까요?")
    #expect(SettingsText.pushTitle(appLabel: "zsh") == "zsh 설정을 폴더로 내보낼까요?")
}

@Test func appLabelsAreTheNamesPeopleKnow() {
    let labels = ["claude", "codex", "bettertouchtool", "ghostty", "herdr", "skills", "zsh"]
        .map { SettingsText.appLabel(name: $0) }
    #expect(labels == ["Claude Code", "Codex", "BetterTouchTool", "Ghostty", "herdr", "스킬", "zsh"])
    #expect(SettingsText.appLabel(name: "newapp") == "newapp")
}

private func change(_ label: String, _ details: String = "", kind: String = "update") -> SyncChange {
    SyncChange(label: label, kind: kind, details: details, diff: nil)
}

@Test func changedFilesPutEachLabelWithItsLineCounts() {
    let changes = [change("settings.json", "+19 −4 · env, hooks, enabledPlugins"), change("installed_plugins.json")]
    #expect(SettingsText.changedFiles(changes) == "settings.json +19 −4 · installed_plugins.json")
    #expect(SettingsText.changedKeys(changes) == "env, hooks, enabledPlugins")
}

@Test func detailsThatAreNotLineCountsStayWithTheLabel() {
    let changes = [change("plugins/", "2 create, 1 update"), change("icon.png", "binary · 1 KB → 2 KB")]
    #expect(SettingsText.changedFiles(changes) == "plugins/ 2 create, 1 update · icon.png binary · 1 KB → 2 KB")
    #expect(SettingsText.changedKeys(changes) == nil)
}

@Test func unchangedEntriesAreNotChanges() {
    #expect(SettingsText.changedFiles([change(".zshrc", kind: "unchanged")]) == nil)
    #expect(SettingsText.changedFiles([]) == nil)
}

@Test func bttLineNamesThePresetsAndWhyTheyWereNotCompared() {
    #expect(SettingsText.bttLine(presets: ["Master_bt"], reason: "btt_not_running") == "프리셋 Master_bt · BTT가 켜져 있어야 비교해요")
    #expect(SettingsText.bttLine(presets: ["A", "B"], reason: "") == "프리셋 A, B")
    #expect(SettingsText.bttLine(presets: [], reason: "btt_permission") == "자동화 권한이 필요해요")
    #expect(SettingsText.bttLine(presets: [], reason: nil) == nil)
}

@Test func directionNoteWarnsWhenThisMacIsNewer() {
    let apps = [
        app("herdr", state: "dirty", direction: "local-newer"),
        app("claude", state: "dirty", direction: "folder-newer"),
    ]
    #expect(SettingsText.directionNote(report(apps)) == "herdr는 이 맥 쪽이 더 최신이에요")
    #expect(SettingsText.directionNote(report([app(state: "dirty", direction: "diverged")])) == nil)
    let both = [app("skills", state: "dirty", direction: "local-newer"),
                app("bettertouchtool", state: "dirty", direction: "local-newer")]
    #expect(SettingsText.directionNote(report(both)) == "스킬, BetterTouchTool은 이 맥 쪽이 더 최신이에요")
    #expect(SettingsText.directionNote(report([app("skills", state: "dirty", direction: "local-newer")]))
        == "스킬은 이 맥 쪽이 더 최신이에요")
}

private func applied(_ results: [ApplyResult]) -> ApplyReport {
    ApplyReport(dir: "/x", direction: "to", results: results)
}

@Test func aCleanApplyHasNoMessage() {
    let report = applied([ApplyResult(app: "zsh", outcome: "changed", error: nil, warnings: [])])
    #expect(SettingsText.applyMessage(report, direction: .pull) == nil)
}

@Test func failedAppsAreNamedWithTheirErrors() {
    let report = applied([
        ApplyResult(app: "zsh", outcome: "changed", error: nil, warnings: []),
        ApplyResult(app: "codex", outcome: "failed", error: "codex timed out after 120 s", warnings: ["skipped x"]),
    ])
    #expect(SettingsText.applyMessage(report, direction: .pull)
        == "1개 앱을 가져오지 못했어요\nCodex: codex timed out after 120 s\nCodex: skipped x")
}

@Test func warningsAloneStillReachThePerson() {
    let report = applied([ApplyResult(app: "claude", outcome: "changed", error: nil, warnings: ["plugin a@m not found"])])
    #expect(SettingsText.applyMessage(report, direction: .push)
        == "내보냈지만 확인할 것이 있어요\nClaude Code: plugin a@m not found")
}

@Test func theCodexRowWaitsForCodexSessionWork() {
    #expect(SettingsText.sessionsLine(app: "codex", codexSessionWork: true) == "세션 정리 중")
    #expect(SettingsText.sessionsLine(app: "codex", codexSessionWork: false) == nil)
    // Claude Code's session runs take no lock a sync waits on.
    #expect(SettingsText.sessionsLine(app: "claude", codexSessionWork: true) == nil)
}
