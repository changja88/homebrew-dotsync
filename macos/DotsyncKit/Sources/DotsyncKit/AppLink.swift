import Foundation

/// dotsync:// links the widget uses to open the app's window at a task.
public enum AppLink: Equatable, Sendable {
    case open
    /// Switch to the account, asking first (the login in use isn't saved).
    case use(String)
    case relogin(String)

    public var url: URL {
        var components = URLComponents()
        components.scheme = "dotsync"
        switch self {
        case .open:
            components.host = "open"
        case .use(let name):
            components.host = "use"
            components.queryItems = [URLQueryItem(name: "name", value: name)]
        case .relogin(let name):
            components.host = "relogin"
            components.queryItems = [URLQueryItem(name: "name", value: name)]
        }
        return components.url!
    }

    public init?(url: URL) {
        guard url.scheme == "dotsync",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let name = components.queryItems?.first { $0.name == "name" }?.value
        switch (components.host, name) {
        case ("open", _): self = .open
        case ("use", let name?): self = .use(name)
        case ("relogin", let name?): self = .relogin(name)
        default: return nil
        }
    }
}
