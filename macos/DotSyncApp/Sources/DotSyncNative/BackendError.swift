import Foundation

public enum BackendError: String, Error, Equatable, Sendable, LocalizedError,
    CustomStringConvertible, CustomDebugStringConvertible {
    case backendNotFound = "backend_not_found"
    case backendStartFailed = "backend_start_failed"
    case backendProtocolError = "backend_protocol_error"
    case backendExited = "backend_exited"

    public var description: String {
        rawValue
    }

    public var debugDescription: String {
        rawValue
    }

    public var errorDescription: String? {
        rawValue
    }
}
