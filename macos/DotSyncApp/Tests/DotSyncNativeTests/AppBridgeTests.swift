import Foundation
import XCTest
@testable import DotSyncNative

final class AppBridgeTests: XCTestCase {
    func testBridgeAcceptsOnlyFixedManagerMessages() throws {
        XCTAssertEqual(
            try AppBridge.decode([
                "action": "open_manager",
                "destination": "overview",
            ]),
            .openManager(.destination(.overview))
        )
        XCTAssertEqual(
            try AppBridge.decode([
                "action": "open_manager",
                "destination": "accounts",
            ]),
            .openManager(.destination(.accounts))
        )
        XCTAssertEqual(
            try AppBridge.decode([
                "action": "open_manager",
                "destination": "settings",
            ]),
            .openManager(.destination(.settings))
        )
        XCTAssertEqual(
            try AppBridge.decode([
                "action": "open_manager",
                "destination": "sync",
                "direction": "backup",
            ]),
            .openManager(.sync(.backup))
        )
        XCTAssertEqual(
            try AppBridge.decode([
                "action": "open_manager",
                "destination": "sync",
                "direction": "apply",
            ]),
            .openManager(.sync(.apply))
        )
    }

    func testBridgeAcceptsOnlyExactOneKeyCommands() throws {
        XCTAssertEqual(
            try AppBridge.decode(["action": "refresh_summary"]),
            .refreshSummary
        )
        XCTAssertEqual(
            try AppBridge.decode(["action": "quit_app"]),
            .quitApp
        )
    }

    func testRemovedLifecycleMessagesCannotDecodeAsNativeCommands() {
        for body in [
            ["action": "manager_sync_listener_ready"],
            ["action": "manager_sync_handoff_received"],
            ["action": "manager_sync_listener_ready", "direction": "apply"],
            ["action": "manager_sync_handoff_received", "sequence": 1],
        ] {
            assertProtocolError(body)
        }
    }

    func testBridgeRejectsMissingAndUnknownManagerValues() {
        let bodies: [Any] = [
            ["action": "open_manager"],
            ["action": "open_manager", "destination": "sync"],
            ["action": "open_manager", "destination": "sync", "direction": "restore"],
            ["action": "open_manager", "destination": "accounts", "direction": "apply"],
            ["action": "open_manager", "destination": "claude"],
            ["action": "open_manager", "destination": 1],
            ["action": "open_manager", "destination": NSNull()],
        ]

        for body in bodies {
            assertProtocolError(body)
        }
    }

    func testBridgeRejectsPathsURLsCommandsProvidersAccountsAndExtraKeys() {
        let bodies: [Any] = [
            ["action": "open_manager", "destination": "sync", "direction": "backup", "path": "/tmp"],
            ["action": "open_manager", "destination": "overview", "url": "https://example.test"],
            ["action": "run", "command": "dotsync backup"],
            ["action": "refresh", "provider": "codex"],
            ["action": "refresh", "account_id": UUID().uuidString],
            ["action": "refresh_summary", "account_id": UUID().uuidString],
            ["action": "quit_app", "reason": "user"],
        ]

        for body in bodies {
            assertProtocolError(body)
        }
    }

    func testBridgeRejectsNonObjectAndNestedBridgedShapes() {
        let bodies: [Any] = [
            "quit_app",
            ["refresh_summary"],
            NSNull(),
            1,
            ["action": ["value": "quit_app"]],
            ["action": ["quit_app"]],
        ]

        for body in bodies {
            assertProtocolError(body)
        }
    }

    func testSyncHandoffReceiverJavaScriptUsesOnlyFixedEnumMappings() {
        XCTAssertEqual(
            ManagerSyncDirection.backup.receiverJavaScript,
            #"window.__dotsyncReceiveManagerSyncHandoff("backup") === true"#
        )
        XCTAssertEqual(
            ManagerSyncDirection.apply.receiverJavaScript,
            #"window.__dotsyncReceiveManagerSyncHandoff("apply") === true"#
        )
    }

    private func assertProtocolError(
        _ body: Any,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        do {
            _ = try AppBridge.decode(body)
            XCTFail("Expected fixed bridge-shape rejection", file: file, line: line)
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
