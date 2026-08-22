import Foundation
import XCTest
@testable import DotSyncNative

final class StrictJSONTests: XCTestCase {
    func testParsesRFC8259ValuesAndExactAccessors() throws {
        let document = try StrictJSONDocument.decode(
            Data(#"{"array":["value",-12,1.5e2,true,false,null]}"#.utf8),
            maximumBytes: 128,
            maximumDepth: 4
        )

        let object = try document.root.exactObject(keys: ["array"])
        let array = try XCTUnwrap(object["array"]).exactArray()
        XCTAssertEqual(try array[0].exactString(), "value")
        XCTAssertEqual(try array[1].exactInteger(), -12)
        XCTAssertEqual(try array[2].exactDouble(), 150)
        XCTAssertEqual(try array[3].exactBoolean(), true)
        XCTAssertEqual(try array[4].exactBoolean(), false)
        XCTAssertNoThrow(try array[5].exactNull())
    }

    func testDuplicateDecodedObjectKeysAreRejected() {
        assertProtocolError(#"{"key":1,"key":2}"#)
        assertProtocolError(#"{"key":1,"k\u0065y":2}"#)
    }

    func testMalformedStringsAndNumbersAreRejected() {
        let sources = [
            #"{"value":"\x"}"#,
            #"{"value":"\uD800"}"#,
            #"{"value":"\uDC00"}"#,
            #"{"value":"\uD800x"}"#,
            #"{"value":01}"#,
            #"{"value":1.}"#,
            #"{"value":1e}"#,
            #"{"value":1e309}"#,
            #"{"value":NaN}"#,
        ]

        for source in sources {
            assertProtocolError(source)
        }
        assertProtocolError(Data([0x22, 0xc3, 0x28, 0x22]))
        assertProtocolError(Data([0x22, 0x01, 0x22]))
    }

    func testBoundsBOMCommentsAndTrailingTokensAreRejected() {
        assertProtocolError(Data(), maximumBytes: 32, maximumDepth: 4)
        assertProtocolError(Data("true".utf8), maximumBytes: 3, maximumDepth: 4)
        assertProtocolError(Data([0xef, 0xbb, 0xbf, 0x6e, 0x75, 0x6c, 0x6c]))
        assertProtocolError("//comment\nnull")
        assertProtocolError("null false")
        assertProtocolError("[[[[[]]]]]", maximumBytes: 32, maximumDepth: 4)
        XCTAssertNoThrow(
            try StrictJSONDocument.decode(
                Data("[[[[]]]]".utf8),
                maximumBytes: 32,
                maximumDepth: 4
            )
        )
    }

    func testExactAccessorsRejectDifferentJSONTypes() throws {
        let document = try StrictJSONDocument.decode(
            Data(#"[1,1.0,"1",null]"#.utf8),
            maximumBytes: 32,
            maximumDepth: 2
        )
        let values = try document.root.exactArray()

        assertProtocolError { _ = try values[0].exactDouble() }
        assertProtocolError { _ = try values[1].exactInteger() }
        assertProtocolError { _ = try values[2].exactNull() }
        assertProtocolError { _ = try values[3].exactString() }
        assertProtocolError { _ = try document.root.exactObject(keys: []) }
    }

    func testFiniteIntegerBeyondNativeIntRemainsAJSONNumber() throws {
        let document = try StrictJSONDocument.decode(
            Data("9223372036854775808".utf8),
            maximumBytes: 32,
            maximumDepth: 1
        )

        XCTAssertEqual(
            try document.root.exactDouble(),
            9_223_372_036_854_775_808
        )
        assertProtocolError { _ = try document.root.exactInteger() }
    }

    private func assertProtocolError(
        _ source: String,
        maximumBytes: Int = 256,
        maximumDepth: Int = 4,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        assertProtocolError(
            Data(source.utf8),
            maximumBytes: maximumBytes,
            maximumDepth: maximumDepth,
            file: file,
            line: line
        )
    }

    private func assertProtocolError(
        _ data: Data,
        maximumBytes: Int = 256,
        maximumDepth: Int = 4,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        assertProtocolError(file: file, line: line) {
            _ = try StrictJSONDocument.decode(
                data,
                maximumBytes: maximumBytes,
                maximumDepth: maximumDepth
            )
        }
    }

    private func assertProtocolError(
        file: StaticString = #filePath,
        line: UInt = #line,
        _ operation: () throws -> Void
    ) {
        do {
            try operation()
            XCTFail("Expected normalized JSON rejection", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                .backendProtocolError,
                "Expected normalized JSON rejection",
                file: file,
                line: line
            )
        }
    }
}
