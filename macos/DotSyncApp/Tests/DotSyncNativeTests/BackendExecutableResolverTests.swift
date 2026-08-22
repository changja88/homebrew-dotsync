import Foundation
import XCTest
@testable import DotSyncNative

final class BackendExecutableResolverTests: XCTestCase {
    func testResolverUsesOnlyFixedHomebrewCandidates() throws {
        let fileSystem = FakeExecutableFileSystem(
            entries: [
                "/opt/homebrew/bin/dotsync":
                    .symlink("/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync"),
                "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync":
                    .regularExecutable,
            ]
        )

        let result = try BackendExecutableResolver(fileSystem: fileSystem).resolve()

        XCTAssertEqual(
            result.path,
            "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync"
        )
        XCTAssertEqual(fileSystem.lookups, ["/opt/homebrew/bin/dotsync"])
    }

    func testResolverFallsBackOnlyToFixedIntelCandidate() throws {
        let fileSystem = FakeExecutableFileSystem(
            entries: [
                "/usr/local/bin/dotsync":
                    .symlink("/usr/local/Cellar/dotsync/0.2.1/bin/dotsync"),
                "/usr/local/Cellar/dotsync/0.2.1/bin/dotsync":
                    .regularExecutable,
            ]
        )

        let result = try BackendExecutableResolver(fileSystem: fileSystem).resolve()

        XCTAssertEqual(
            result.path,
            "/usr/local/Cellar/dotsync/0.2.1/bin/dotsync"
        )
        XCTAssertEqual(
            fileSystem.lookups,
            ["/opt/homebrew/bin/dotsync", "/usr/local/bin/dotsync"]
        )
    }

    func testResolverNeverSearchesPathOrAcceptsOutsideCellarSymlink() {
        let fileSystem = FakeExecutableFileSystem(
            entries: [
                "/opt/homebrew/bin/dotsync": .symlink("/tmp/evil"),
                "/tmp/evil": .regularExecutable,
            ]
        )

        assertNotFound(BackendExecutableResolver(fileSystem: fileSystem))

        XCTAssertEqual(
            fileSystem.lookups,
            ["/opt/homebrew/bin/dotsync", "/usr/local/bin/dotsync"]
        )
    }

    func testResolverRejectsPrefixConfusionWrongCellarAndUnsafeFileKinds() {
        let targets = [
            "/opt/homebrew/Cellar/dotsync-evil/0.2.1/bin/dotsync":
                FakeExecutableFileSystem.Entry.regularExecutable,
            "/opt/homebrew/Cellar/other/0.2.1/bin/dotsync":
                .regularExecutable,
            "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync":
                .regularNotExecutable,
            "/usr/local/Cellar/dotsync/0.2.1/bin/dotsync":
                .directory,
        ]

        for (target, entry) in targets {
            let candidate = target.hasPrefix("/usr/local/")
                ? "/usr/local/bin/dotsync"
                : "/opt/homebrew/bin/dotsync"
            let fileSystem = FakeExecutableFileSystem(
                entries: [candidate: .symlink(target), target: entry]
            )
            assertNotFound(BackendExecutableResolver(fileSystem: fileSystem))
        }
    }

    func testInjectedOverrideRequiresTheExactRegularExecutableIdentity() throws {
        let executable = "/private/tmp/native-host-fixture"
        let fileSystem = FakeExecutableFileSystem(
            entries: [executable: .regularExecutable]
        )
        let resolver = BackendExecutableResolver(fileSystem: fileSystem)

        let result = try resolver.resolve(
            testOverride: URL(fileURLWithPath: executable)
        )

        XCTAssertEqual(result.path, executable)
        XCTAssertEqual(fileSystem.lookups, [executable])
    }

    func testInjectedOverrideRejectsSymlinksAndDoesNotFallBack() {
        let override = "/private/tmp/native-host-link"
        let target = "/private/tmp/native-host-fixture"
        let fileSystem = FakeExecutableFileSystem(
            entries: [
                override: .symlink(target),
                target: .regularExecutable,
                "/opt/homebrew/bin/dotsync":
                    .symlink("/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync"),
                "/opt/homebrew/Cellar/dotsync/0.2.1/bin/dotsync":
                    .regularExecutable,
            ]
        )

        assertNotFound(
            BackendExecutableResolver(fileSystem: fileSystem),
            testOverride: URL(fileURLWithPath: override)
        )

        XCTAssertEqual(fileSystem.lookups, [override])
    }

    func testNativeErrorsRenderOnlyNormalizedCodes() {
        let errors: [BackendError] = [
            .backendNotFound,
            .backendStartFailed,
            .backendProtocolError,
            .backendExited,
        ]

        XCTAssertEqual(
            errors.map(String.init(describing:)),
            [
                "backend_not_found",
                "backend_start_failed",
                "backend_protocol_error",
                "backend_exited",
            ]
        )
        XCTAssertEqual(
            errors.map(\.localizedDescription),
            errors.map(\.rawValue)
        )
    }

    private func assertNotFound(
        _ resolver: BackendExecutableResolver,
        testOverride: URL? = nil,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        do {
            _ = try resolver.resolve(testOverride: testOverride)
            XCTFail("Expected normalized executable rejection", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                .backendNotFound,
                "Expected normalized executable rejection",
                file: file,
                line: line
            )
        }
    }
}

private final class FakeExecutableFileSystem: ExecutableFileSystem,
    @unchecked Sendable {
    enum Entry {
        case symlink(String)
        case regularExecutable
        case regularNotExecutable
        case directory
    }

    private let entries: [String: Entry]
    private(set) var lookups: [String] = []

    init(entries: [String: Entry]) {
        self.entries = entries
    }

    func inspectExecutable(at url: URL) throws -> ExecutableFileInspection? {
        let source = url.path
        lookups.append(source)
        var current = source
        var followed: Set<String> = []

        while case let .symlink(target)? = entries[current] {
            guard followed.insert(current).inserted else { return nil }
            current = target
        }
        guard let entry = entries[current] else { return nil }
        switch entry {
        case .symlink:
            return nil
        case .regularExecutable:
            return ExecutableFileInspection(
                resolvedURL: URL(fileURLWithPath: current),
                isRegularFile: true,
                isExecutable: true
            )
        case .regularNotExecutable:
            return ExecutableFileInspection(
                resolvedURL: URL(fileURLWithPath: current),
                isRegularFile: true,
                isExecutable: false
            )
        case .directory:
            return ExecutableFileInspection(
                resolvedURL: URL(fileURLWithPath: current),
                isRegularFile: false,
                isExecutable: true
            )
        }
    }
}
