import Foundation
import Testing
@testable import DotsyncKit

private func app(_ name: String = "zsh", tracked: Bool = true, state: String?, direction: String? = "",
                 reason: String? = "") -> AppSyncStatus {
    AppSyncStatus(name: name, description: "", tracked: tracked, state: state, details: "",
                  direction: direction, reason: reason, changes: [])
}

private func report(_ apps: [AppSyncStatus]) -> StatusReport {
    StatusReport(dir: "/x", dirSource: "rc", checkedAt: Date(timeIntervalSince1970: 0), apps: apps)
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
