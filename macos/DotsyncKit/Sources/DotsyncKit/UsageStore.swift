import Foundation

/// usage.json: written by the app, read by the widget.
public struct UsageStore: Sendable {
    public static let appGroup = "GR53VV7ZD2.dotsync"

    public let fileURL: URL

    public init(directory: URL) {
        fileURL = directory.appendingPathComponent("usage.json")
    }

    /// The store in the app group container the widget can read.
    public static func shared() -> UsageStore? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroup)
            .map(UsageStore.init(directory:))
    }

    /// nil before the first save, or when the file can't be read.
    public func load() -> UsageFile? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? UsageJSON.decoder().decode(UsageFile.self, from: data)
    }

    /// Atomic: the widget never reads half a file.
    public func save(_ file: UsageFile) throws {
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try UsageJSON.encoder().encode(file).write(to: fileURL, options: .atomic)
    }
}
