import Foundation

/// dotsync:// links the widget uses to open the app's window at a task, and
/// the one the auto-switch checker wakes the app with (no window).
public enum AppLink: Equatable, Sendable {
    case open
    /// Switch to the account, asking first (the login in use isn't saved).
    case use(String)
    case relogin(String)
    case autoSwitch(AutoSwitchRequest)

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
        case .autoSwitch(let request):
            components.host = "auto-switch"
            components.queryItems = [URLQueryItem(name: "why", value: request.rawValue)]
        }
        return components.url!
    }

    public init?(url: URL) {
        guard url.scheme == "dotsync",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let name = components.queryItems?.first { $0.name == "name" }?.value
        let why = components.queryItems?.first { $0.name == "why" }?.value.flatMap(AutoSwitchRequest.init(rawValue:))
        switch (components.host, name, why) {
        case ("open", _, _): self = .open
        case ("use", let name?, _): self = .use(name)
        case ("relogin", let name?, _): self = .relogin(name)
        case ("auto-switch", _, let why?): self = .autoSwitch(why)
        default: return nil
        }
    }
}
