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
