import Foundation
import SwiftUI

public struct MenuSummary: Equatable, Sendable {
    public enum State: String, Equatable, Sendable {
        case fresh
        case stale
        case unknown
    }

    public struct Usage: Equatable, Sendable {
        public let state: State
        public let highestPercent: Double?

        public init(state: State, highestPercent: Double?) {
            self.state = state
            self.highestPercent = highestPercent
        }
    }

    public struct Sync: Equatable, Sendable {
        public let state: State
        public let attentionCount: Int?

        public init(state: State, attentionCount: Int?) {
            self.state = state
            self.attentionCount = attentionCount
        }
    }

    public let usage: Usage
    public let sync: Sync
    public let observedAt: Date?

    public init(usage: Usage, sync: Sync, observedAt: Date?) {
        self.usage = usage
        self.sync = sync
        self.observedAt = observedAt
    }

    public static let unknown = MenuSummary(
        usage: Usage(state: .unknown, highestPercent: nil),
        sync: Sync(state: .unknown, attentionCount: nil),
        observedAt: nil
    )

    public static func decode(_ data: Data) throws -> MenuSummary {
        let document = try StrictJSONDocument.decode(
            data,
            maximumBytes: 16_384,
            maximumDepth: 8
        )
        let root = try document.root.exactObject(
            keys: ["usage", "sync", "observed_at"]
        )
        guard let usageValue = root["usage"],
              let syncValue = root["sync"],
              let observedValue = root["observed_at"]
        else { throw BackendError.backendProtocolError }

        let usageObject = try usageValue.exactObject(
            keys: ["state", "highest_percent"]
        )
        let syncObject = try syncValue.exactObject(
            keys: ["state", "attention_count"]
        )
        guard let usageStateValue = usageObject["state"],
              let percentValue = usageObject["highest_percent"],
              let syncStateValue = syncObject["state"],
              let countValue = syncObject["attention_count"],
              let usageState = State(
                rawValue: try usageStateValue.exactString()
              ),
              let syncState = State(
                rawValue: try syncStateValue.exactString()
              )
        else { throw BackendError.backendProtocolError }

        let highestPercent = try optionalPercentage(
            percentValue,
            state: usageState
        )
        let attentionCount = try optionalAttentionCount(
            countValue,
            state: syncState
        )
        let observedAt = try optionalRFC3339(observedValue)
        guard (usageState == .unknown && syncState == .unknown)
            || observedAt != nil
        else { throw BackendError.backendProtocolError }

        return MenuSummary(
            usage: Usage(
                state: usageState,
                highestPercent: highestPercent
            ),
            sync: Sync(
                state: syncState,
                attentionCount: attentionCount
            ),
            observedAt: observedAt
        )
    }
}

@MainActor
public final class MenuSummaryModel: ObservableObject {
    @Published public private(set) var summary: MenuSummary

    public init(summary: MenuSummary = .unknown) {
        self.summary = summary
    }

    public var menuTitle: String {
        guard let value = summary.usage.highestPercent
        else { return "DotSync · —" }
        let suffix = summary.usage.state == .stale ? " stale" : ""
        return "DotSync · \(Int(value.rounded()))%\(suffix)"
    }

    public func accept(_ summary: MenuSummary) {
        self.summary = summary
    }

    public func acceptMalformedResponse() {
        summary = .unknown
    }
}

public protocol MenuSummaryFetching: Sendable {
    func fetch() async throws -> MenuSummary
}

public struct MenuSummaryClient: MenuSummaryFetching, Sendable {
    private let origin: LocalOrigin
    private let session: URLSession

    public init(origin: LocalOrigin) {
        self.origin = origin
        self.session = URLSession(
            configuration: Self.makeSessionConfiguration(),
            delegate: MenuSummarySessionDelegate(),
            delegateQueue: nil
        )
    }

    init(origin: LocalOrigin, session: URLSession) {
        self.origin = origin
        self.session = session
    }

    static func makeSessionConfiguration(
        protocolClasses: [AnyClass]? = nil
    ) -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        if let protocolClasses {
            configuration.protocolClasses = protocolClasses
        }
        return configuration
    }

    public func fetch() async throws -> MenuSummary {
        let url = origin.baseURL.appendingPathComponent("api/menu-summary")
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        origin.authorize(&request)
        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  httpResponse.url == url,
                  data.count <= 16_384
            else { throw BackendError.backendProtocolError }
            return try MenuSummary.decode(data)
        } catch let error as BackendError {
            throw error
        } catch {
            throw BackendError.backendProtocolError
        }
    }
}

final class MenuSummarySessionDelegate: NSObject, URLSessionTaskDelegate,
    @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

public struct MenuSummaryFetchResult: Equatable, Sendable {
    public let generation: UInt64
    public let summary: MenuSummary

    fileprivate init(generation: UInt64, summary: MenuSummary) {
        self.generation = generation
        self.summary = summary
    }
}

public struct MenuSummaryOwnedFetchResult: Equatable, Sendable {
    public let result: MenuSummaryFetchResult
    public let requestOwner: UInt64

    fileprivate init(result: MenuSummaryFetchResult, requestOwner: UInt64) {
        self.result = result
        self.requestOwner = requestOwner
    }
}

public actor MenuSummaryPoller {
    public typealias MonotonicNow = @Sendable () -> Duration

    private let fetcher: any MenuSummaryFetching
    private let monotonicNow: MonotonicNow
    private var lastAttempt: Duration?
    private var generation: UInt64 = 0

    public init(fetcher: any MenuSummaryFetching) {
        let clock = ContinuousClock()
        let origin = clock.now
        self.fetcher = fetcher
        self.monotonicNow = {
            origin.duration(to: clock.now)
        }
    }

    public init(
        fetcher: any MenuSummaryFetching,
        monotonicNow: @escaping MonotonicNow
    ) {
        self.fetcher = fetcher
        self.monotonicNow = monotonicNow
    }

    public func poll(isActive: Bool) async -> MenuSummaryFetchResult? {
        guard reservePoll(isActive: isActive)
        else { return nil }
        return await fetchResult(generation: beginFetch())
    }

    public func poll(
        isActive: Bool,
        requestStarted: @MainActor @Sendable () -> UInt64
    ) async -> MenuSummaryOwnedFetchResult? {
        await poll(
            isActive: isActive,
            requestStartedAsync: { requestStarted() }
        )
    }

    func poll(
        isActive: Bool,
        requestStartedAsync: @MainActor @Sendable () async -> UInt64
    ) async -> MenuSummaryOwnedFetchResult? {
        guard reservePoll(isActive: isActive)
        else { return nil }
        let requestOwner = await requestStartedAsync()
        let ownedGeneration = beginFetch()
        let result = await fetchResult(generation: ownedGeneration)
        return MenuSummaryOwnedFetchResult(
            result: result,
            requestOwner: requestOwner
        )
    }

    public func reload() async -> MenuSummaryFetchResult {
        lastAttempt = monotonicNow()
        return await fetchResult(generation: beginFetch())
    }

    public func ownsNewest(_ result: MenuSummaryFetchResult) -> Bool {
        result.generation == generation
    }

    private func reservePoll(isActive: Bool) -> Bool {
        guard isActive else { return false }
        let now = monotonicNow()
        if let lastAttempt,
           now - lastAttempt < .seconds(60)
        {
            return false
        }
        lastAttempt = now
        return true
    }

    private func beginFetch() -> UInt64 {
        generation &+= 1
        return generation
    }

    private func fetchResult(generation ownedGeneration: UInt64) async
        -> MenuSummaryFetchResult {
        let summary: MenuSummary
        do {
            summary = try await fetcher.fetch()
        } catch {
            summary = .unknown
        }
        return MenuSummaryFetchResult(
            generation: ownedGeneration,
            summary: summary
        )
    }
}

private func optionalPercentage(
    _ value: StrictJSONValue,
    state: MenuSummary.State
) throws -> Double? {
    if state == .unknown {
        try value.exactNull()
        return nil
    }

    let percentage: Double
    switch value {
    case let .double(number):
        percentage = number
    case let .integer(number):
        percentage = Double(number)
    default:
        throw BackendError.backendProtocolError
    }
    guard percentage.isFinite, (0...100).contains(percentage)
    else { throw BackendError.backendProtocolError }
    return percentage
}

private func optionalAttentionCount(
    _ value: StrictJSONValue,
    state: MenuSummary.State
) throws -> Int? {
    if state == .unknown {
        try value.exactNull()
        return nil
    }

    let count = try value.exactInteger()
    guard (0...10_000).contains(count)
    else { throw BackendError.backendProtocolError }
    return count
}

private func optionalRFC3339(_ value: StrictJSONValue) throws -> Date? {
    if case .null = value {
        return nil
    }
    let source = try value.exactString()
    guard let parts = RFC3339Parts(source: source),
          let localDate = parts.localDate
    else { throw BackendError.backendProtocolError }
    return localDate.addingTimeInterval(
        parts.fractionalSeconds - TimeInterval(parts.offsetSeconds)
    )
}

private struct RFC3339Parts {
    let year: Int
    let month: Int
    let day: Int
    let hour: Int
    let minute: Int
    let second: Int
    let fractionalSeconds: TimeInterval
    let offsetSeconds: Int

    init?(source: String) {
        let pattern = #"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|([+-])([0-9]{2}):([0-9]{2}))$"#
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(
                in: source,
                range: NSRange(source.startIndex..., in: source)
              ),
              match.range == NSRange(source.startIndex..., in: source),
              let year = Self.integer(match, group: 1, source: source),
              let month = Self.integer(match, group: 2, source: source),
              let day = Self.integer(match, group: 3, source: source),
              let hour = Self.integer(match, group: 4, source: source),
              let minute = Self.integer(match, group: 5, source: source),
              let second = Self.integer(match, group: 6, source: source),
              (0...23).contains(hour),
              (0...59).contains(minute),
              (0...59).contains(second)
        else { return nil }

        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self.minute = minute
        self.second = second

        if let fraction = Self.string(match, group: 7, source: source) {
            guard let value = Double("0." + fraction)
            else { return nil }
            fractionalSeconds = value
        } else {
            fractionalSeconds = 0
        }

        guard let zone = Self.string(match, group: 8, source: source)
        else { return nil }
        if zone == "Z" {
            offsetSeconds = 0
        } else {
            guard let sign = Self.string(match, group: 9, source: source),
                  let offsetHour = Self.integer(
                    match,
                    group: 10,
                    source: source
                  ),
                  let offsetMinute = Self.integer(
                    match,
                    group: 11,
                    source: source
                  ),
                  (0...23).contains(offsetHour),
                  (0...59).contains(offsetMinute)
            else { return nil }
            let seconds = offsetHour * 3_600 + offsetMinute * 60
            guard !(sign == "-" && seconds == 0) else { return nil }
            offsetSeconds = sign == "-" ? -seconds : seconds
        }
    }

    var localDate: Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        var components = DateComponents()
        components.calendar = calendar
        components.timeZone = calendar.timeZone
        components.year = year
        components.month = month
        components.day = day
        components.hour = hour
        components.minute = minute
        components.second = second
        guard let date = calendar.date(from: components) else { return nil }
        let verified = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: date
        )
        guard verified.year == year,
              verified.month == month,
              verified.day == day,
              verified.hour == hour,
              verified.minute == minute,
              verified.second == second
        else { return nil }
        return date
    }

    private static func integer(
        _ match: NSTextCheckingResult,
        group: Int,
        source: String
    ) -> Int? {
        string(match, group: group, source: source).flatMap(Int.init)
    }

    private static func string(
        _ match: NSTextCheckingResult,
        group: Int,
        source: String
    ) -> String? {
        let range = match.range(at: group)
        guard range.location != NSNotFound,
              let swiftRange = Range(range, in: source)
        else { return nil }
        return String(source[swiftRange])
    }
}
