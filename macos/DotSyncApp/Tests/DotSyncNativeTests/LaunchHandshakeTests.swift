import Foundation
import XCTest
@testable import DotSyncNative

final class LaunchHandshakeTests: XCTestCase {
    private let validToken = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    func testValidHandshakeDecodesExactOriginAndCapability() throws {
        let line = Data(
            #"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#.utf8
        )

        let value = try LaunchHandshake.decode(line)

        XCTAssertEqual(value.schemaVersion, 1)
        XCTAssertEqual(value.origin.baseURL.absoluteString, "http://127.0.0.1:49152")
    }

    func testDuplicateMissingExtraAndEscapedFieldsAreRejected() {
        assertProtocolError(
            #"{"schema_version":1,"origin":"http://127.0.0.1:49152","origin":"http://127.0.0.1:49153","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#
        )
        assertProtocolError(
            #"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","path":"/tmp"}"#
        )
        assertProtocolError(
            #"{"schema_version":1,"origin":"http://127.0.0.1:49152"}"#
        )
        assertProtocolError(
            #"{"schema_version":1,"orig\u0069n":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#
        )
        assertProtocolError(
            #"{"schema_version":1,"Origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#
        )
    }

    func testInvalidFramingAndEncodingAreRejected() {
        assertProtocolError(Data())
        assertProtocolError(Data(repeating: 0x61, count: 4_097))
        assertProtocolError(Data(#"{"schema_version":1}"#.utf8) + Data([0x0a]))
        assertProtocolError(Data(#"{"schema_version":1}"#.utf8) + Data([0x0d]))
        assertProtocolError(Data(#"{"schema_version":1}"#.utf8) + Data([0x0a, 0x7b, 0x7d]))
        assertProtocolError(Data([0xff, 0xfe]))
        assertProtocolError(Data([0xef, 0xbb, 0xbf]) + validLine())
        assertProtocolError(Data((#"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":""}"# + " trailing").utf8))
        assertProtocolError(Data(#"/*comment*/{"schema_version":1}"#.utf8))
    }

    func testSchemaVersionMustBeExactIntegerOne() {
        assertProtocolError(validLine(version: "1.0"))
        assertProtocolError(validLine(version: "1e0"))
        assertProtocolError(validLine(version: "0"))
        assertProtocolError(validLine(version: "2"))
        assertProtocolError(validLine(version: "true"))
    }

    func testOriginMustBeExactLoopbackHTTPAuthority() {
        let invalidOrigins = [
            "https://127.0.0.1:49152",
            "HTTP://127.0.0.1:49152",
            "http://localhost:49152",
            "http://[::1]:49152",
            "http://0.0.0.0:49152",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://user@127.0.0.1:49152",
            "http://user:password@127.0.0.1:49152",
            "http://127.0.0.1:49152/api",
            "http://127.0.0.1:49152/?query=1",
            "http://127.0.0.1:49152/#fragment",
        ]

        for origin in invalidOrigins {
            assertProtocolError(validLine(origin: origin))
        }
    }

    func testCapabilityMustBeBase64URLForExactlyThirtyTwoBytes() {
        let invalidTokens = [
            "",
            String(repeating: "A", count: 42),
            String(repeating: "A", count: 44),
            String(repeating: "A", count: 42) + "+",
            String(repeating: "A", count: 42) + "=",
            String(repeating: "A", count: 42) + "é",
        ]

        for token in invalidTokens {
            assertProtocolError(validLine(token: token))
        }
    }

    private func validLine(
        version: String = "1",
        origin: String = "http://127.0.0.1:49152",
        token: String? = nil
    ) -> Data {
        let capability = token ?? validToken
        return Data(
            #"{"schema_version":\#(version),"origin":"\#(origin)","token":"\#(capability)"}"#.utf8
        )
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
            _ = try LaunchHandshake.decode(data)
            XCTFail("Expected normalized protocol rejection", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                .backendProtocolError,
                "Expected normalized protocol rejection",
                file: file,
                line: line
            )
        }
    }
}
