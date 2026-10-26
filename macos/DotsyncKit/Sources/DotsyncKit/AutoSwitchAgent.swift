import Foundation

/// The LaunchAgent behind auto switch: launchd runs the app's own executable
/// in check mode whenever the app group folder changes (claude-hud writes
/// live-usage.json there after each answer). The app writes the plist with
/// real paths when auto switch is turned on — launchd expands no `~`, so a
/// fixed plist inside the app (SMAppService.agent) can't name the folder.
public enum AutoSwitchAgent {
    public static let label = "com.changja88.dotsync.autoswitch"
    /// The first argument that makes the app's executable check and exit.
    public static let checkArgument = "--auto-switch-check"
    /// Names the agent "dotsync" in System Settings' login items.
    public static let appBundleID = "com.changja88.dotsync"

    public static func plistURL(home: URL) -> URL {
        home.appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    /// What reconciling the installed agent with the setting takes. A
    /// translocated app runs from a random copy gone at its next launch, so
    /// its path is never written.
    public static func action(enabled: Bool, installed: AgentPlist?, wanted: AgentPlist) -> AgentAction {
        guard enabled else { return installed == nil ? .none : .remove }
        guard !wanted.executable.contains("/AppTranslocation/") else { return .none }
        return installed == wanted ? .none : .install
    }
}

/// The plist's own content: which executable runs, which folder it watches.
public struct AgentPlist: Equatable, Sendable {
    public var executable: String
    public var watched: String

    public init(executable: URL, watched: URL) {
        self.executable = executable.path
        self.watched = watched.path
    }

    /// nil for a file that isn't this agent's plist.
    public init?(data: Data) {
        guard let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let arguments = plist["ProgramArguments"] as? [String], let executable = arguments.first,
              let watched = (plist["WatchPaths"] as? [String])?.first
        else { return nil }
        self.executable = executable
        self.watched = watched
    }

    /// No RunAtLoad or KeepAlive: the check runs only when the folder
    /// changes, at most every 10 s (launchd's default ThrottleInterval).
    public func data() throws -> Data {
        let plist: [String: Any] = [
            "Label": AutoSwitchAgent.label,
            "ProgramArguments": [executable, AutoSwitchAgent.checkArgument],
            "WatchPaths": [watched],
            "AssociatedBundleIdentifiers": [AutoSwitchAgent.appBundleID],
        ]
        return try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
    }
}

public enum AgentAction: Equatable, Sendable {
    case none
    case install
    case remove
}

/// What the 자동 교체 menu says about the agent.
public enum AgentState: Equatable, Sendable {
    case off
    case on
    /// Turned off in System Settings → Login Items & Extensions.
    case needsApproval
    /// On, but launchd doesn't have it: the plist is missing or not loaded.
    case notInstalled
}
