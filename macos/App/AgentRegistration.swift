import DotsyncKit
import Foundation
import ServiceManagement

/// Puts the auto-switch LaunchAgent into launchd and takes it out again
/// (`AutoSwitchAgent` decides its content). `launchctl` runs as the person,
/// in their GUI domain: no password. It blocks while launchctl works, so
/// callers run it off the main actor.
enum AgentRegistration {
    struct Failure: Error {
        var message: String
    }

    private static var plistURL: URL {
        AutoSwitchAgent.plistURL(home: FileManager.default.homeDirectoryForCurrentUser)
    }

    private static var service: String { "gui/\(getuid())/\(AutoSwitchAgent.label)" }

    /// The plist for this copy of the app; nil without the app group folder.
    static func wanted() -> AgentPlist? {
        guard let executable = Bundle.main.executableURL,
              let folder = UsageStore.shared()?.fileURL.deletingLastPathComponent() else { return nil }
        return AgentPlist(executable: executable, watched: folder)
    }

    static func install(_ plist: AgentPlist) throws {
        try FileManager.default.createDirectory(
            at: plistURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try plist.data().write(to: plistURL, options: .atomic)
        _ = launchctl(["bootout", service])
        // bootout finishes on its own time, and bootstrapping a label that is
        // still loaded fails.
        for _ in 0..<30 where launchctl(["print", service]).status == 0 {
            Thread.sleep(forTimeInterval: 0.1)
        }
        let result = launchctl(["bootstrap", "gui/\(getuid())", plistURL.path])
        guard result.status == 0 else { throw Failure(message: result.error) }
    }

    static func remove() {
        _ = launchctl(["bootout", service])
        try? FileManager.default.removeItem(at: plistURL)
    }

    /// A loaded agent reads `.enabled` (spike 1d); one turned off in System
    /// Settings asks for approval.
    static func state(enabled: Bool) -> AgentState {
        guard enabled else { return .off }
        guard FileManager.default.fileExists(atPath: plistURL.path) else { return .notInstalled }
        switch SMAppService.statusForLegacyPlist(at: plistURL) {
        case .enabled: return .on
        case .requiresApproval: return .needsApproval
        default: return .notInstalled
        }
    }

    /// At launch: the agent matches the setting and this copy of the app
    /// (moved, or removed by a Homebrew upgrade until the app is opened).
    static func reconcile(enabled: Bool) {
        guard let wanted = wanted() else { return }
        let installed = (try? Data(contentsOf: plistURL)).flatMap(AgentPlist.init(data:))
        switch AutoSwitchAgent.action(enabled: enabled, installed: installed, wanted: wanted) {
        case .none: break
        case .install: try? install(wanted)
        case .remove: remove()
        }
    }

    private static func launchctl(_ arguments: [String]) -> (status: Int32, error: String) {
        let process = Process()
        let errors = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = errors
        do {
            try process.run()
        } catch {
            return (-1, error.localizedDescription)
        }
        process.waitUntilExit()
        let text = String(decoding: errors.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return (process.terminationStatus, text.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}
