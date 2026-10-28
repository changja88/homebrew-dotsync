import Foundation
import Testing
@testable import DotsyncKit

private let installed = URL(fileURLWithPath: "/Applications/dotsync.app/Contents/MacOS/dotsync")
private let folder = URL(fileURLWithPath: "/Users/me/Library/Group Containers/GR53VV7ZD2.dotsync")

@Test func thePlistRunsTheAppInCheckModeWhenTheFolderChanges() throws {
    let data = try AgentPlist(executable: installed, watched: folder).data()
    let plist = try #require(
        PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])

    #expect(plist["Label"] as? String == "com.changja88.dotsync.autoswitch")
    #expect(plist["ProgramArguments"] as? [String]
        == ["/Applications/dotsync.app/Contents/MacOS/dotsync", "--auto-switch-check"])
    #expect(plist["WatchPaths"] as? [String] == ["/Users/me/Library/Group Containers/GR53VV7ZD2.dotsync"])
    #expect(plist["AssociatedBundleIdentifiers"] as? [String] == ["com.changja88.dotsync"])
    #expect(plist["RunAtLoad"] == nil)
    #expect(plist["KeepAlive"] == nil)
}

@Test func thePlistReadsBack() throws {
    let plist = AgentPlist(executable: installed, watched: folder)
    #expect(AgentPlist(data: try plist.data()) == plist)
    #expect(AgentPlist(data: Data("not a plist".utf8)) == nil)
}

@Test func thePlistLivesInTheUsersLaunchAgents() {
    #expect(AutoSwitchAgent.plistURL(home: URL(fileURLWithPath: "/Users/me")).path
        == "/Users/me/Library/LaunchAgents/com.changja88.dotsync.autoswitch.plist")
}

@Test func reconcileInstallsReplacesAndRemoves() {
    let wanted = AgentPlist(executable: installed, watched: folder)
    let elsewhere = AgentPlist(executable: URL(fileURLWithPath: "/Users/me/Applications/dotsync.app/Contents/MacOS/dotsync"),
                               watched: folder)
    #expect(AutoSwitchAgent.action(enabled: true, installed: nil, wanted: wanted) == .install)
    #expect(AutoSwitchAgent.action(enabled: true, installed: elsewhere, wanted: wanted) == .install)
    #expect(AutoSwitchAgent.action(enabled: true, installed: wanted, wanted: wanted) == .none)
    #expect(AutoSwitchAgent.action(enabled: false, installed: wanted, wanted: wanted) == .remove)
    #expect(AutoSwitchAgent.action(enabled: false, installed: nil, wanted: wanted) == .none)
}

@Test func reconcileNeverWritesATranslocatedPath() {
    // A quarantined app run from where it was downloaded runs from a random
    // read-only copy; that path is gone at the next launch.
    let translocated = AgentPlist(
        executable: URL(fileURLWithPath: "/private/var/folders/x/AppTranslocation/y/d/dotsync.app/Contents/MacOS/dotsync"),
        watched: folder)
    #expect(AutoSwitchAgent.action(enabled: true, installed: nil, wanted: translocated) == .none)
}

/// launchctl as the tests script it: `print` answers "still loaded" a given
/// number of times, then "not found".
private final class FakeLaunchctl {
    var calls: [[String]] = []
    var loadedPrints: Int
    var bootstrap: (status: Int32, error: String)

    init(loadedPrints: Int, bootstrap: (status: Int32, error: String) = (0, "")) {
        self.loadedPrints = loadedPrints
        self.bootstrap = bootstrap
    }

    func run(_ arguments: [String]) -> (status: Int32, error: String) {
        calls.append(arguments)
        switch arguments.first {
        case "print":
            guard loadedPrints > 0 else { return (113, "Could not find service") }
            loadedPrints -= 1
            return (0, "")
        case "bootstrap": return bootstrap
        default: return (0, "")
        }
    }
}

@Test func installStopsWaitingOnceLaunchdHasLetGo() throws {
    let url = try temporaryDirectory().appendingPathComponent("LaunchAgents/agent.plist")
    let launchctl = FakeLaunchctl(loadedPrints: 2)
    var pauses = 0
    let plist = AgentPlist(executable: installed, watched: folder)

    try AutoSwitchAgent.install(plist, at: url, uid: 501, launchctl: launchctl.run, pause: { pauses += 1 })

    let service = "gui/501/com.changja88.dotsync.autoswitch"
    #expect(launchctl.calls == [["bootout", service], ["print", service], ["print", service], ["print", service],
                                ["bootstrap", "gui/501", url.path]])
    #expect(pauses == 2)
    #expect(AgentPlist(data: try Data(contentsOf: url)) == plist)
}

@Test func installWaitsThreeSecondsAtMostForTheOldAgentToGo() throws {
    let url = try temporaryDirectory().appendingPathComponent("agent.plist")
    let launchctl = FakeLaunchctl(loadedPrints: .max)

    try AutoSwitchAgent.install(AgentPlist(executable: installed, watched: folder), at: url, uid: 501,
                                launchctl: launchctl.run, pause: {})

    #expect(launchctl.calls.filter { $0.first == "print" }.count == 30)
    #expect(launchctl.calls.last?.first == "bootstrap")
}

@Test func aFailedBootstrapLeavesNoPlistBehind() throws {
    // A plist launchd never took would read as "on but not loaded" until the
    // app is opened again.
    let url = try temporaryDirectory().appendingPathComponent("agent.plist")
    let launchctl = FakeLaunchctl(loadedPrints: 0, bootstrap: (5, "Bootstrap failed: 5: Input/output error"))

    #expect(throws: AutoSwitchAgent.InstallFailure(message: "Bootstrap failed: 5: Input/output error")) {
        try AutoSwitchAgent.install(AgentPlist(executable: installed, watched: folder), at: url, uid: 501,
                                    launchctl: launchctl.run, pause: {})
    }
    #expect(!FileManager.default.fileExists(atPath: url.path))
}

// Login items record an agent launchd just loaded only seconds later
// (2026-10-28: "record not found" right after bootstrap, found 8 s on).

@Test func anAgentLaunchdHasLoadedIsOnBeforeLoginItemsRecordIt() {
    #expect(AutoSwitchAgent.state(background: .noRecord, isLoaded: { true }) == .on)
}

@Test func withNoRecordAndNothingInLaunchdTheAgentIsGone() {
    #expect(AutoSwitchAgent.state(background: .noRecord, isLoaded: { false }) == .notInstalled)
}

@Test func aRecordInLoginItemsDecidesWithoutAskingLaunchd() {
    var asked = 0
    let isLoaded = { asked += 1; return false }
    #expect(AutoSwitchAgent.state(background: .enabled, isLoaded: isLoaded) == .on)
    #expect(AutoSwitchAgent.state(background: .requiresApproval, isLoaded: isLoaded) == .needsApproval)
    #expect(asked == 0)
}
