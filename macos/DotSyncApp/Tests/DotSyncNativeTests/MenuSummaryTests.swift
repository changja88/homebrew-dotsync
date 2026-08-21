import Foundation
import XCTest
@testable import DotSyncNative

final class MenuSummaryTests: XCTestCase {
    func testSummaryDecodesExactSafeDTO() throws {
        let data = Data(
            #"{"usage":{"state":"stale","highest_percent":72.0},"sync":{"state":"fresh","attention_count":1},"observed_at":"2026-08-21T09:00:00Z"}"#.utf8
        )

        let summary = try MenuSummary.decode(data)

        XCTAssertEqual(summary.usage.state, .stale)
        XCTAssertEqual(summary.usage.highestPercent, 72.0)
        XCTAssertEqual(summary.sync.state, .fresh)
        XCTAssertEqual(summary.sync.attentionCount, 1)
        XCTAssertNotNil(summary.observedAt)
    }

    func testSummaryAcceptsExactUnknownAndMixedStatePairings() throws {
        let unknown = try decode(
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#
        )
        let mixed = try decode(
            #"{"usage":{"state":"stale","highest_percent":0.0},"sync":{"state":"unknown","attention_count":null},"observed_at":"2026-08-21T09:00:00+09:00"}"#
        )

        XCTAssertEqual(unknown, .unknown)
        XCTAssertEqual(mixed.usage.highestPercent, 0)
        XCTAssertNil(mixed.sync.attentionCount)
        XCTAssertNotNil(mixed.observedAt)
    }

    func testSummaryRejectsExtraDuplicateMissingAndIdentityBearingKeys() {
        let rejected = [
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":null,"path":"/tmp"}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":null,"provider":"codex"}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":null,"account_id":"00000000-0000-0000-0000-000000000000"}"#,
            #"{"usage":{"state":"unknown","highest_percent":null,"label":"Personal"},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#,
            #"{"usage":{"state":"unknown","highest_percent":null,"identity":{"email":"person@example.test"}},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#,
            #"{"usage":{"state":"unknown","state":"fresh","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":null}}"#,
        ]

        for source in rejected {
            assertProtocolError(source)
        }
    }

    func testSummaryRejectsInvalidStateValuePairings() {
        let rejected = [
            #"{"usage":{"state":"unknown","highest_percent":0.0},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#,
            #"{"usage":{"state":"fresh","highest_percent":null},"sync":{"state":"unknown","attention_count":null},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"other","highest_percent":1.0},"sync":{"state":"unknown","attention_count":null},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"unknown","attention_count":0},"observed_at":null}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"fresh","attention_count":null},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"unknown","highest_percent":null},"sync":{"state":"other","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":1.0},"sync":{"state":"unknown","attention_count":null},"observed_at":null}"#,
        ]

        for source in rejected {
            assertProtocolError(source)
        }
    }

    func testSummaryRejectsOutOfRangeAndWrongNumericTypes() {
        let rejected = [
            #"{"usage":{"state":"fresh","highest_percent":-0.1},"sync":{"state":"fresh","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":100.1},"sync":{"state":"fresh","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":1e309},"sync":{"state":"fresh","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":"72"},"sync":{"state":"fresh","attention_count":0},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":72.0},"sync":{"state":"fresh","attention_count":-1},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":72.0},"sync":{"state":"fresh","attention_count":10001},"observed_at":"2026-08-21T09:00:00Z"}"#,
            #"{"usage":{"state":"fresh","highest_percent":72.0},"sync":{"state":"fresh","attention_count":1.0},"observed_at":"2026-08-21T09:00:00Z"}"#,
        ]

        for source in rejected {
            assertProtocolError(source)
        }
        XCTAssertNoThrow(
            try decode(
                #"{"usage":{"state":"fresh","highest_percent":100.0},"sync":{"state":"fresh","attention_count":10000},"observed_at":"2026-08-21T09:00:00Z"}"#
            )
        )
    }

    func testSummaryAcceptsCanonicalRFC3339AndRejectsNormalizedOrTrailingDates() {
        for timestamp in [
            "2026-08-21T09:00:00Z",
            "2026-08-21T09:00:00.123456789Z",
            "2026-08-21T09:00:00+09:00",
            "2026-08-21T09:00:00-04:30",
        ] {
            XCTAssertNoThrow(try decode(summary(observedAt: timestamp)))
        }

        for timestamp in [
            "2026-02-29T09:00:00Z",
            "2026-13-01T09:00:00Z",
            "2026-08-21 09:00:00Z",
            "2026-08-21T09:00Z",
            "2026-08-21T09:00:00z",
            "2026-08-21T09:00:00+0900",
            "2026-08-21T09:00:00-00:00",
            "2026-08-21T24:00:00Z",
            "2026-08-21T09:00:00Z trailing",
        ] {
            assertProtocolError(summary(observedAt: timestamp))
        }
    }

    func testSummaryBoundsResponseSize() {
        assertProtocolError(Data(repeating: 0x20, count: 16_385))
    }

    @MainActor
    func testUnknownOrMalformedSummaryNeverDisplaysZero() {
        let model = MenuSummaryModel()

        model.acceptMalformedResponse()

        XCTAssertEqual(model.menuTitle, "DotSync · —")
        XCTAssertEqual(model.summary, .unknown)
    }

    @MainActor
    func testMenuTitleRoundsKnownValueAndMarksStale() throws {
        let summary = try decode(
            #"{"usage":{"state":"stale","highest_percent":72.4},"sync":{"state":"fresh","attention_count":1},"observed_at":"2026-08-21T09:00:00Z"}"#
        )
        let model = MenuSummaryModel()

        model.accept(summary)

        XCTAssertEqual(model.menuTitle, "DotSync · 72% stale")
    }

    private func decode(_ source: String) throws -> MenuSummary {
        try MenuSummary.decode(Data(source.utf8))
    }

    private func summary(observedAt: String) -> String {
        "{\"usage\":{\"state\":\"fresh\",\"highest_percent\":1.0},"
            + "\"sync\":{\"state\":\"fresh\",\"attention_count\":0},"
            + "\"observed_at\":\"" + observedAt + "\"}"
    }

    private func assertProtocolError(
        _ source: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        assertProtocolError(Data(source.utf8), file: file, line: line)
    }

    private func assertProtocolError(
        _ data: Data,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        do {
            _ = try MenuSummary.decode(data)
            XCTFail("Expected normalized menu-summary rejection", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                .backendProtocolError,
                file: file,
                line: line
            )
        }
    }
}

final class MenuSummaryClientTests: XCTestCase {
    private let token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    override func tearDown() {
        SummaryURLProtocol.handler = nil
        super.tearDown()
    }

    func testClientUsesOnlyAuthorizedMenuSummaryGETWithEphemeralStorage() async throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
        let configuration = MenuSummaryClient.makeSessionConfiguration(
            protocolClasses: [SummaryURLProtocol.self]
        )
        let session = URLSession(configuration: configuration)
        let requestBox = LockedRequestBox()
        SummaryURLProtocol.handler = { request in
            requestBox.set(request)
            let response = HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            )!
            let body = Data(
                #"{"usage":{"state":"fresh","highest_percent":72.0},"sync":{"state":"fresh","attention_count":1},"observed_at":"2026-08-21T09:00:00Z"}"#.utf8
            )
            return (response, body)
        }
        let client = MenuSummaryClient(origin: origin, session: session)

        let result = try await client.fetch()

        XCTAssertEqual(result.usage.highestPercent, 72)
        let request = try XCTUnwrap(requestBox.value)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:49152/api/menu-summary")
        XCTAssertNil(request.url?.query)
        XCTAssertTrue(
            request.value(forHTTPHeaderField: "X-DotSync-Token") == token
        )
        XCTAssertEqual(
            request.cachePolicy,
            .reloadIgnoringLocalAndRemoteCacheData
        )
        XCTAssertNil(configuration.identifier)
        XCTAssertNil(configuration.urlCache)
        XCTAssertNil(configuration.httpCookieStorage)
        XCTAssertEqual(
            configuration.requestCachePolicy,
            .reloadIgnoringLocalAndRemoteCacheData
        )
    }

    func testClientRejectsNonSuccessAndOversizedResponses() async throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
        let configuration = MenuSummaryClient.makeSessionConfiguration(
            protocolClasses: [SummaryURLProtocol.self]
        )
        let session = URLSession(configuration: configuration)
        let client = MenuSummaryClient(origin: origin, session: session)

        SummaryURLProtocol.handler = { request in
            (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 500,
                    httpVersion: nil,
                    headerFields: nil
                )!,
                Data()
            )
        }
        await assertProtocolError { try await client.fetch() }

        SummaryURLProtocol.handler = { request in
            (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: nil
                )!,
                Data(repeating: 0x20, count: 16_385)
            )
        }
        await assertProtocolError { try await client.fetch() }
    }

    func testClientNormalizesTransportFailureWithoutRetainingRawDetail() async throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
        let configuration = MenuSummaryClient.makeSessionConfiguration(
            protocolClasses: [SummaryURLProtocol.self]
        )
        let client = MenuSummaryClient(
            origin: origin,
            session: URLSession(configuration: configuration)
        )
        SummaryURLProtocol.handler = { _ in
            throw NSError(
                domain: "raw-transport-secret",
                code: 19,
                userInfo: [NSLocalizedDescriptionKey: "token path account"]
            )
        }

        await assertProtocolError { try await client.fetch() }
    }

    func testSummarySessionCancelsRedirectBeforeFollowingAnotherRouteOrOrigin() throws {
        let delegate = MenuSummarySessionDelegate()
        let session = URLSession(
            configuration: .ephemeral,
            delegate: delegate,
            delegateQueue: nil
        )
        let task = session.dataTask(
            with: URL(string: "http://127.0.0.1:49152/api/menu-summary")!
        )
        let response = HTTPURLResponse(
            url: task.originalRequest!.url!,
            statusCode: 302,
            httpVersion: "HTTP/1.1",
            headerFields: ["Location": "/api/accounts"]
        )!
        var redirect: URLRequest? = URLRequest(
            url: URL(string: "https://example.test/collect")!
        )

        delegate.urlSession(
            session,
            task: task,
            willPerformHTTPRedirection: response,
            newRequest: URLRequest(
                url: URL(string: "http://127.0.0.1:49152/api/accounts")!
            ),
            completionHandler: { redirect = $0 }
        )

        XCTAssertNil(redirect)
        session.invalidateAndCancel()
    }

    func testPollerRunsAtMostEverySixtySecondsOnlyWhileActiveAndExplicitReloadBypassesCadence() async throws {
        let fetcher = CountingSummaryFetcher()
        let clock = ManualSummaryMonotonicClock()
        let poller = MenuSummaryPoller(
            fetcher: fetcher,
            monotonicNow: { clock.now }
        )

        let inactive = await poller.poll(isActive: false)
        let first = await poller.poll(isActive: true)
        clock.advance(by: .seconds(59) + .milliseconds(999))
        let tooSoon = await poller.poll(isActive: true)
        clock.advance(by: .milliseconds(1))
        let atBoundary = await poller.poll(isActive: true)
        clock.advance(by: .seconds(1))
        let explicit = await poller.reload()
        XCTAssertNil(inactive)
        XCTAssertNotNil(first)
        XCTAssertNil(tooSoon)
        XCTAssertNotNil(atBoundary)
        XCTAssertEqual(explicit.summary, .unknown)
        let fetchCount = await fetcher.count
        XCTAssertEqual(fetchCount, 3)
    }

    func testPollerCadenceDoesNotDriftAcrossForwardAndBackwardWallClockChanges() async {
        let fetcher = CountingSummaryFetcher()
        let clock = ManualSummaryMonotonicClock()
        let poller = MenuSummaryPoller(
            fetcher: fetcher,
            monotonicNow: { clock.now }
        )
        var wallClock = Date(timeIntervalSince1970: 1_800_000_000)

        let initial = await poller.poll(isActive: true)
        XCTAssertNotNil(initial)
        wallClock = wallClock.addingTimeInterval(86_400)
        clock.advance(by: .seconds(30))
        let afterForwardJump = await poller.poll(isActive: true)
        XCTAssertNil(afterForwardJump)
        wallClock = wallClock.addingTimeInterval(-172_800)
        clock.advance(by: .seconds(29))
        let afterBackwardJump = await poller.poll(isActive: true)
        XCTAssertNil(afterBackwardJump)
        clock.advance(by: .seconds(1))
        let atMonotonicBoundary = await poller.poll(isActive: true)
        XCTAssertNotNil(atMonotonicBoundary)

        XCTAssertEqual(wallClock, Date(timeIntervalSince1970: 1_799_913_600))
        let fetchCount = await fetcher.count
        XCTAssertEqual(fetchCount, 2)
    }

    private func assertProtocolError(
        _ operation: () async throws -> MenuSummary,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        do {
            _ = try await operation()
            XCTFail("Expected normalized client rejection", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                .backendProtocolError,
                file: file,
                line: line
            )
        }
    }
}

private final class LockedRequestBox: @unchecked Sendable {
    private let lock = NSLock()
    private var request: URLRequest?

    var value: URLRequest? {
        lock.withLock { request }
    }

    func set(_ request: URLRequest) {
        lock.withLock { self.request = request }
    }
}

private final class SummaryURLProtocol: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        do {
            guard let handler = Self.handler else {
                throw BackendError.backendProtocolError
            }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private actor CountingSummaryFetcher: MenuSummaryFetching {
    private(set) var count = 0

    func fetch() async throws -> MenuSummary {
        count += 1
        return .unknown
    }
}

private final class ManualSummaryMonotonicClock: @unchecked Sendable {
    private let lock = NSLock()
    private var instant = Duration.zero

    var now: Duration {
        lock.withLock { instant }
    }

    func advance(by duration: Duration) {
        lock.withLock { instant += duration }
    }
}
