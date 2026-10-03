import Foundation

/// One usage window (5-hour or weekly) as dotsync reports it.
public struct UsageWindow: Codable, Equatable, Sendable {
    public var percent: Int
    public var resetsAt: Date?

    public init(percent: Int, resetsAt: Date?) {
        self.percent = percent
        self.resetsAt = resetsAt
    }
}

public enum AccountStatus: String, Codable, Sendable {
    case ok
    case loginRequired = "login_required"
    case error
}

/// A saved account's usage: one entry of `dotsync account usage --json`, or
/// of usage.json, where `fetchedAt` says when its values were read.
public struct AccountUsage: Codable, Equatable, Sendable, Identifiable {
    public var name: String
    public var label: String
    public var email: String?
    public var status: AccountStatus
    public var error: String?
    public var fiveHour: UsageWindow?
    public var sevenDay: UsageWindow?
    public var fetchedAt: Date?

    public var id: String { name }
}

/// The login Claude uses when it matches no saved account.
public struct UnsavedSeat: Codable, Equatable, Sendable {
    public var email: String?
    public var status: AccountStatus
    public var error: String?
    public var fiveHour: UsageWindow?
    public var sevenDay: UsageWindow?
}

/// `dotsync account usage --json`.
public struct UsageReport: Codable, Equatable, Sendable {
    public var fetchedAt: Date
    public var active: String?
    public var unsavedSeat: UnsavedSeat?
    public var accounts: [AccountUsage]
}

/// usage.json in the app group — what the widget draws.
public struct UsageFile: Codable, Equatable, Sendable {
    public var version: Int
    public var fetchedAt: Date?
    public var lastError: String?
    public var active: String?
    public var unsavedSeat: UnsavedSeat?
    public var accounts: [AccountUsage]

    public static let empty = UsageFile(
        version: 1, fetchedAt: nil, lastError: nil, active: nil, unsavedSeat: nil, accounts: [])

    /// The saved account Claude uses now, if any.
    public var activeAccount: AccountUsage? {
        accounts.first { $0.name == active }
    }
}

/// `login`, `use` and `rename` answer with one of these.
public struct AccountInfo: Codable, Equatable, Sendable {
    public var name: String
    public var label: String
    public var email: String?
    public var loggedIn: Bool
}

/// dotsync's `--json` failure: `{"error": {"code", "message", "email"?}}`.
public struct CLIError: Error, Codable, Equatable, Sendable {
    public var code: String
    public var message: String
    public var email: String?

    public init(code: String, message: String, email: String? = nil) {
        self.code = code
        self.message = message
        self.email = email
    }
}

struct CLIErrorEnvelope: Decodable {
    var error: CLIError
}

public enum UsageJSON {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}
