import Foundation

/// auto-switch.json and auto-switch-log.json, next to usage.json: the app
/// writes them, the checker and the widget read them.
public struct AutoSwitchStore: Sendable {
    public let settingsURL: URL
    public let logURL: URL

    /// The newest entries the log keeps.
    static let logLimit = 50

    public init(directory: URL) {
        settingsURL = directory.appendingPathComponent("auto-switch.json")
        logURL = directory.appendingPathComponent("auto-switch-log.json")
    }

    /// The store in the app group container, beside usage.json.
    public static func shared() -> AutoSwitchStore? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: UsageStore.appGroup)
            .map(AutoSwitchStore.init(directory:))
    }

    /// Off until it is turned on, and when the file can't be read.
    public func loadSettings() -> AutoSwitchSettings {
        guard let data = try? Data(contentsOf: settingsURL) else { return .off }
        return (try? UsageJSON.decoder().decode(AutoSwitchSettings.self, from: data)) ?? .off
    }

    public func save(_ settings: AutoSwitchSettings) throws {
        try write(settings, to: settingsURL)
    }

    /// Reads the file again and changes it: a run that took seconds keeps
    /// what the person turned on, off or chose meanwhile.
    public func updateSettings(_ change: (inout AutoSwitchSettings) -> Void) throws {
        var settings = loadSettings()
        change(&settings)
        try save(settings)
    }

    /// Newest first.
    public func loadLog() -> [AutoSwitchLogEntry] {
        guard let data = try? Data(contentsOf: logURL) else { return [] }
        return (try? UsageJSON.decoder().decode([AutoSwitchLogEntry].self, from: data)) ?? []
    }

    public func append(_ entry: AutoSwitchLogEntry) throws {
        try write(Array(([entry] + loadLog()).prefix(Self.logLimit)), to: logURL)
    }

    /// Atomic: the widget and the checker never read half a file.
    private func write<Value: Encodable>(_ value: Value, to url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try UsageJSON.encoder().encode(value).write(to: url, options: .atomic)
    }
}
