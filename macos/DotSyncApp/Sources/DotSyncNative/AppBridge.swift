import Foundation

public enum ManagerSyncDirection: String, Equatable, Sendable {
    case backup
    case apply

    public var eventJavaScript: String {
        switch self {
        case .backup:
            return #"window.dispatchEvent(new CustomEvent("dotsync:manager-sync-preview",{detail:{direction:"backup"}}));"#
        case .apply:
            return #"window.dispatchEvent(new CustomEvent("dotsync:manager-sync-preview",{detail:{direction:"apply"}}));"#
        }
    }
}

public enum ManagerRequest: Equatable, Sendable {
    case destination(LocalOrigin.Destination)
    case sync(ManagerSyncDirection)

    public var destination: LocalOrigin.Destination {
        switch self {
        case let .destination(destination):
            return destination
        case .sync:
            return .sync
        }
    }
}

public enum NativeCommand: Equatable, Sendable {
    case openManager(ManagerRequest)
    case refreshSummary
    case quitApp
}

enum AppBridgeMessage: Equatable, Sendable {
    case command(NativeCommand)
    case managerSyncListenerReady
    case managerSyncHandoffReceived
}

public enum AppBridge {
    public static func decode(_ body: Any) throws -> NativeCommand {
        guard case let .command(command) = try decodeMessage(body)
        else { throw BackendError.backendProtocolError }
        return command
    }

    static func decodeMessage(_ body: Any) throws -> AppBridgeMessage {
        guard let object = body as? [String: Any],
              let action = object["action"] as? String
        else { throw BackendError.backendProtocolError }

        switch action {
        case "open_manager":
            return .command(try decodeManagerRequest(object))
        case "refresh_summary":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .command(.refreshSummary)
        case "quit_app":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .command(.quitApp)
        case "manager_sync_listener_ready":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .managerSyncListenerReady
        case "manager_sync_handoff_received":
            guard Set(object.keys) == Set(["action"])
            else { throw BackendError.backendProtocolError }
            return .managerSyncHandoffReceived
        default:
            throw BackendError.backendProtocolError
        }
    }

    private static func decodeManagerRequest(
        _ object: [String: Any]
    ) throws -> NativeCommand {
        guard let rawDestination = object["destination"] as? String,
              let destination = LocalOrigin.Destination(
                rawValue: rawDestination
              )
        else { throw BackendError.backendProtocolError }

        if destination == .sync {
            guard Set(object.keys) == Set([
                "action",
                "destination",
                "direction",
            ]),
            let rawDirection = object["direction"] as? String,
            let direction = ManagerSyncDirection(rawValue: rawDirection)
            else { throw BackendError.backendProtocolError }
            return .openManager(.sync(direction))
        }

        guard Set(object.keys) == Set(["action", "destination"])
        else { throw BackendError.backendProtocolError }
        return .openManager(.destination(destination))
    }
}
