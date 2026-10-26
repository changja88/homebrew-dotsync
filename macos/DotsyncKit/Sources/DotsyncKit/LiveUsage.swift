import Foundation

/// The usage Claude Code hands its status line after each answer, as
/// claude-hud's `display.externalUsageWritePath` writes it: live-usage.json
/// next to usage.json. It names no account; `UsageMerge.withLive` decides
/// whose numbers they are.
public struct LiveUsage: Equatable, Sendable {
    public var updatedAt: Date
    public var fiveHour: UsageWindow?
    public var sevenDay: UsageWindow?
}

extension LiveUsage {
    /// nil when the file isn't a snapshot with its time.
    public init?(json: Data) {
        guard let file = try? Self.decoder.decode(File.self, from: json) else { return nil }
        self.init(updatedAt: file.updatedAt, fiveHour: file.fiveHour?.window, sevenDay: file.sevenDay?.window)
    }

    private struct File: Decodable {
        var updatedAt: Date
        var fiveHour: Window?
        var sevenDay: Window?
    }

    private struct Window: Decodable {
        var usedPercentage: Double
        var resetsAt: Date?

        var window: UsageWindow { UsageWindow(percent: Int(usedPercentage.rounded()), resetsAt: resetsAt) }
    }

    /// claude-hud writes JavaScript's toISOString(), with milliseconds, which
    /// the `.iso8601` strategy rejects.
    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            for options: ISO8601DateFormatter.Options in [[.withInternetDateTime, .withFractionalSeconds],
                                                          [.withInternetDateTime]] {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = options
                if let date = formatter.date(from: text) { return date }
            }
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                    debugDescription: "not an ISO 8601 time: \(text)"))
        }
        return decoder
    }()
}
