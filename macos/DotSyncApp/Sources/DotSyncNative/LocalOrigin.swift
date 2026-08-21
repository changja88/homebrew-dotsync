import Foundation

public struct LocalOrigin: Equatable, Sendable,
    CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public enum Surface: String, CaseIterable, Sendable {
        case popover
        case manager
    }

    public enum Destination: String, CaseIterable, Sendable {
        case overview
        case accounts
        case sync
        case settings
    }

    public let baseURL: URL
    private let token: String

    public init(origin: String, token: String) throws {
        let allowed = CharacterSet(
            charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        )
        guard token.utf8.count == 43,
              token.unicodeScalars.allSatisfy({ allowed.contains($0) })
        else { throw BackendError.backendProtocolError }
        let padded = token
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/") + "="
        guard Data(base64Encoded: padded)?.count == 32,
              let components = URLComponents(string: origin),
              components.scheme == "http",
              components.host == "127.0.0.1",
              let port = components.port,
              (1...65_535).contains(port),
              components.user == nil,
              components.password == nil,
              components.path.isEmpty || components.path == "/",
              components.query == nil,
              components.fragment == nil,
              origin == "http://127.0.0.1:\(port)" + (components.path == "/" ? "/" : ""),
              let url = components.url
        else { throw BackendError.backendProtocolError }
        self.baseURL = url
        self.token = token
    }

    public func launchURL(
        surface: Surface,
        destination: Destination = .overview
    ) throws -> URL {
        guard var components = URLComponents(
            url: baseURL,
            resolvingAgainstBaseURL: false
        ) else { throw BackendError.backendProtocolError }
        components.path = "/"
        components.queryItems = [
            URLQueryItem(name: "token", value: token),
            URLQueryItem(name: "surface", value: surface.rawValue),
            URLQueryItem(name: "destination", value: destination.rawValue),
        ]
        guard let result = components.url
        else { throw BackendError.backendProtocolError }
        return result
    }

    public func accepts(_ url: URL) -> Bool {
        guard let port = baseURL.port else { return false }
        let serialized = url.absoluteString
        let root = "http://127.0.0.1:\(port)"
        if serialized == root || serialized == root + "/" { return true }
        return Surface.allCases.contains { surface in
            Destination.allCases.contains { destination in
                guard let expected = try? launchURL(
                    surface: surface,
                    destination: destination
                ) else { return false }
                return serialized == expected.absoluteString
            }
        }
    }

    public func authorize(_ request: inout URLRequest) {
        request.setValue(token, forHTTPHeaderField: "X-DotSync-Token")
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    }

    public var description: String {
        "LocalOrigin"
    }

    public var debugDescription: String {
        description
    }

    public var customMirror: Mirror {
        Mirror(
            self,
            children: ["baseURL": baseURL],
            displayStyle: .struct
        )
    }
}
