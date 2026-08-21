import Foundation
import XCTest
@testable import DotSyncNative

final class LocalOriginTests: XCTestCase {
    private let token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    func testLaunchURLUsesOnlyFixedRootAndAllowlistedContext() throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )

        let url = try origin.launchURL(surface: .manager, destination: .settings)

        XCTAssertEqual(
            url.absoluteString,
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=settings"
        )
        XCTAssertTrue(origin.accepts(url))
        XCTAssertTrue(origin.accepts(URL(string: "http://127.0.0.1:49152")!))
        XCTAssertTrue(origin.accepts(URL(string: "http://127.0.0.1:49152/")!))
    }

    func testAcceptsOnlyRootAndExactGeneratedLaunchQueries() throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
        let rejected = [
            "http://127.0.0.1:49152/api",
            "http://127.0.0.1:49152/?surface=manager&token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&destination=settings",
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=settings&extra=1",
            "http://127.0.0.1:49152/?token=wrong&surface=manager&destination=settings",
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=other&destination=settings",
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=other",
            "http://127.0.0.1:49152/?tok%65n=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=settings",
            "http://127.0.0.1:49152/?token=%41AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=settings",
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=man%61ger&destination=settings",
            "http://127.0.0.1:49152/?token=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&surface=manager&destination=sett%69ngs",
            "http://127.0.0.1:49152/%2F",
            "http://127.0.0.1:49153/",
            "http://localhost:49152/",
            "http://2130706433:49152/",
            "http://0x7f000001:49152/",
            "http://127.1:49152/",
            "https://127.0.0.1:49152/",
            "http://user@127.0.0.1:49152/",
            "http://127.0.0.1:49152/#fragment",
        ]

        for source in rejected {
            XCTAssertFalse(origin.accepts(URL(string: source)!))
        }
    }

    func testAuthorizeUsesOnlyCapabilityHeaderAndDisablesCaching() throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )
        var request = URLRequest(url: URL(string: "http://127.0.0.1:49152/api")!)

        origin.authorize(&request)

        XCTAssertEqual(request.value(forHTTPHeaderField: "X-DotSync-Token"), token)
        XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalAndRemoteCacheData)
    }

    func testDescriptionAndDebugDescriptionDoNotRevealCapability() throws {
        let origin = try LocalOrigin(
            origin: "http://127.0.0.1:49152",
            token: token
        )

        XCTAssertFalse(String(describing: origin).contains(token))
        XCTAssertFalse(String(reflecting: origin).contains(token))
        var reflection = ""
        dump(origin, to: &reflection)
        XCTAssertFalse(reflection.contains(token))
    }
}
