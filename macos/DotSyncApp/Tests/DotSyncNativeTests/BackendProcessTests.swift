import Foundation
import XCTest
@testable import DotSyncNative

final class BackendProcessTests: XCTestCase {
    private var fixtures: [NativeHostFixture] = []

    override func tearDownWithError() throws {
        for fixture in fixtures {
            XCTAssertTrue(
                fixture.waitUntilStopped(timeout: .seconds(2)),
                "Fixture process survived its owning test"
            )
            fixture.cleanup()
        }
        fixtures.removeAll()
        try super.tearDownWithError()
    }

    func testStartUsesStdoutHandshakeAndStdinLifetimePipe() async throws {
        let fixture = try makeFixture(.valid)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )

        let session = try backend.start()

        XCTAssertEqual(session.origin.baseURL.host, "127.0.0.1")
        XCTAssertTrue(fixture.waitForArguments(timeout: .seconds(1)))
        XCTAssertTrue(fixture.isRunning)

        try await backend.stop()

        XCTAssertTrue(fixture.waitForControlEOF(timeout: .seconds(1)))
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testNoHandshakeBeforeDeadlineIsProtocolErrorAndStopsChild() throws {
        let fixture = try makeFixture(.noHandshake)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .milliseconds(100)
        )

        assertStartError(.backendProtocolError, backend: backend)

        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testHandshakeBeyondMaximumLineIsProtocolErrorAndStopsChild() throws {
        let fixture = try makeFixture(.oversizedHandshake)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )

        assertStartError(.backendProtocolError, backend: backend)

        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testEOFBeforeHandshakeLineIsProtocolErrorAndStopsChild() throws {
        let fixture = try makeFixture(.eofBeforeLineFeed)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )

        assertStartError(.backendProtocolError, backend: backend)

        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testSecondStdoutByteAfterHandshakeIsProtocolErrorAndStopsChild() throws {
        let fixture = try makeFixture(.extraStdout)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )

        assertStartError(.backendProtocolError, backend: backend)

        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testDelayedStdoutByteAfterStartNotifiesProtocolErrorExactlyOnce() async throws {
        let fixture = try makeFixture(.extraStdoutAfterRelease)
        let events = LockedBox<[BackendError]>([])
        let callback = DispatchSemaphore(value: 0)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1),
            onUnexpectedExit: { error in
                events.withValue { $0.append(error) }
                callback.signal()
            }
        )
        _ = try backend.start()

        try fixture.release()

        XCTAssertEqual(callback.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
        try await backend.stop()
        XCTAssertEqual(events.value, [.backendProtocolError])
    }

    func testChildExitBeforeHandshakeIsBackendExited() throws {
        let fixture = try makeFixture(.exitBeforeHandshake)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )

        assertStartError(.backendExited, backend: backend)

        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testChildExitAfterStartUpdatesStateExactlyOnce() async throws {
        let fixture = try makeFixture(.exitAfterRelease)
        let events = LockedBox<[BackendError]>([])
        let callback = DispatchSemaphore(value: 0)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1),
            onUnexpectedExit: { error in
                events.withValue { $0.append(error) }
                callback.signal()
            }
        )
        _ = try backend.start()

        try fixture.release()

        XCTAssertEqual(callback.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
        try await backend.stop()
        XCTAssertEqual(events.value, [.backendExited])
    }

    func testStopEscalatesFromControlEOFToTermThenKill() async throws {
        let fixture = try makeFixture(.ignoresShutdown)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )
        _ = try backend.start()
        let started = DispatchTime.now()

        try await backend.stop()

        let elapsed = Double(DispatchTime.now().uptimeNanoseconds - started.uptimeNanoseconds)
            / 1_000_000_000
        XCTAssertGreaterThanOrEqual(elapsed, 2.8)
        XCTAssertTrue(fixture.receivedTerm)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testStopIsIdempotentWhenCalledConcurrently() async throws {
        let fixture = try makeFixture(.valid)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )
        _ = try backend.start()
        try await withThrowingTaskGroup(of: Void.self) { group in
            for _ in 0..<8 {
                group.addTask {
                    try await backend.stop()
                }
            }
            try await group.waitForAll()
        }

        XCTAssertTrue(fixture.waitedForControlEOF)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testConcurrentStartAndStopAreSerialized() async throws {
        let fixture = try makeFixture(.handshakeAfterRelease)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(2)
        )
        let session = LockedBox<BackendSession?>(nil)
        let startError = LockedBox<BackendError?>(nil)
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async {
            do {
                session.withValue { $0 = try? backend.start() }
            }
            if session.value == nil {
                startError.withValue { $0 = .backendStartFailed }
            }
            group.leave()
        }
        XCTAssertTrue(fixture.waitForReady(timeout: .seconds(1)))
        let stopAttempted = DispatchSemaphore(value: 0)
        let stopTask = Task {
            stopAttempted.signal()
            try await backend.stop()
        }
        XCTAssertEqual(stopAttempted.wait(timeout: .now() + 1), .success)

        try fixture.release()

        XCTAssertEqual(group.wait(timeout: .now() + 2), .success)
        try await stopTask.value
        XCTAssertNotNil(session.value)
        XCTAssertNil(startError.value)
        XCTAssertTrue(fixture.waitedForControlEOF)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testSecondStartIsRejectedWithoutReplacingOwnedChild() async throws {
        let fixture = try makeFixture(.valid)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )
        _ = try backend.start()

        assertStartError(.backendStartFailed, backend: backend)

        XCTAssertTrue(fixture.isRunning)
        try await backend.stop()
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testStartDuringStillRunningStopCannotLaunchASecondChild() async throws {
        let fixture = try makeFixture(.ignoresShutdown)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )
        _ = try backend.start()
        let stopTask = Task {
            try await backend.stop()
        }
        XCTAssertTrue(fixture.waitForTerm(timeout: .seconds(4)))

        assertStartError(.backendStartFailed, backend: backend)

        XCTAssertEqual(fixture.launchCount, 1)
        try await stopTask.value
        XCTAssertEqual(fixture.launchCount, 1)
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testAwaitedStopReapsOnBackgroundExecutorWithoutBlockingMainActor() async throws {
        let fixture = try makeFixture(.valid)
        let system = FirstWaitBarrierSystem()
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1),
            system: system
        )
        _ = try backend.start()
        let stopTask = Task { @MainActor in
            try await backend.stop()
        }
        XCTAssertEqual(system.waitEntered.wait(timeout: .now() + 1), .success)

        let mainActorAdvanced = await Task { @MainActor in
            true
        }.value

        XCTAssertTrue(mainActorAdvanced)
        system.allowWait.signal()
        try await stopTask.value
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testPhysicalExitBeforeStopIntentReportsUnexpectedExitExactlyOnce() async throws {
        let fixture = try makeFixture(.exitAfterRelease)
        let terminationReached = DispatchSemaphore(value: 0)
        let allowTerminationRecord = DispatchSemaphore(value: 0)
        let events = LockedBox<[BackendError]>([])
        let callback = DispatchSemaphore(value: 0)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1),
            onUnexpectedExit: { error in
                events.withValue { $0.append(error) }
                callback.signal()
            },
            system: FoundationBackendProcessSystem(),
            testHooks: BackendProcessTestHooks(
                beforeTerminationRecord: {
                    terminationReached.signal()
                    allowTerminationRecord.wait()
                }
            )
        )
        _ = try backend.start()
        try fixture.release()
        XCTAssertEqual(terminationReached.wait(timeout: .now() + 1), .success)

        let stopTask = Task {
            try await backend.stop()
        }

        XCTAssertEqual(callback.wait(timeout: .now() + 1), .success)
        allowTerminationRecord.signal()
        try await stopTask.value
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
        XCTAssertEqual(events.value, [.backendExited])
    }

    func testUnconfirmedFinalKillRetainsOwnerUntilBackgroundReaperConfirmsExit() async throws {
        let fixture = try makeFixture(.ignoresShutdown)
        let system = FailFirstKillSystem()
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1),
            system: system
        )
        _ = try backend.start()

        do {
            try await backend.stop()
            XCTFail("Expected normalized unconfirmed-exit failure")
        } catch {
            XCTAssertEqual(error as? BackendError, .backendExited)
            XCTAssertEqual(error.localizedDescription, "backend_exited")
            XCTAssertFalse(error.localizedDescription.contains(fixture.directoryURL.path))
        }
        let retainedStop = Task {
            try await backend.stop()
        }
        XCTAssertEqual(system.retryEntered.wait(timeout: .now() + 2), .success)

        assertStartError(.backendStartFailed, backend: backend)

        XCTAssertEqual(fixture.launchCount, 1)
        system.allowRetry.signal()
        try await retainedStop.value
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testDeinitClosesControlPipeAndLeavesNoChild() throws {
        let fixture = try makeFixture(.valid)
        var backend: BackendProcess? = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(1)
        )
        _ = try backend?.start()

        backend = nil

        XCTAssertTrue(fixture.waitForControlEOF(timeout: .seconds(1)))
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    func testStderrIsDiscardedAndSensitiveEnvironmentIsRemoved() async throws {
        let fixture = try makeFixture(.stderrFlood)
        let backend = BackendProcess(
            testOverride: fixture.executableURL,
            handshakeTimeout: .seconds(2)
        )
        let source = [
            "HOME": "/Users/example",
            "TMPDIR": "/private/tmp/example",
            "LANG": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "__CF_USER_TEXT_ENCODING": "0x1F5:0:0",
            "XPC_FLAGS": "0x0",
            "XPC_SERVICE_NAME": "application.test",
            "PATH": "/tmp/untrusted",
            "PYTHONPATH": "/tmp/injected",
            "OPENAI_API_KEY": "provider-secret",
            "ANTHROPIC_AUTH_TOKEN": "provider-secret",
            "CODEX_HOME": "/tmp/provider-home",
            "OPENAI_BASE_URL": "https://provider.invalid",
            "BASH_FUNC_attack%%": "() { :; }",
        ]

        let environment = sanitizedBackendEnvironment(from: source)
        _ = try backend.start()

        XCTAssertEqual(
            environment,
            [
                "HOME": "/Users/example",
                "TMPDIR": "/private/tmp/example",
                "LANG": "en_US.UTF-8",
                "LC_CTYPE": "UTF-8",
                "__CF_USER_TEXT_ENCODING": "0x1F5:0:0",
                "XPC_FLAGS": "0x0",
                "XPC_SERVICE_NAME": "application.test",
            ]
        )
        XCTAssertTrue(fixture.waitForArguments(timeout: .seconds(1)))
        try await backend.stop()
        XCTAssertTrue(fixture.waitUntilStopped(timeout: .seconds(1)))
    }

    private func makeFixture(_ mode: NativeHostFixture.Mode) throws -> NativeHostFixture {
        let fixture = try NativeHostFixture(mode: mode)
        fixtures.append(fixture)
        return fixture
    }

    private func assertStartError(
        _ expected: BackendError,
        backend: BackendProcess,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        do {
            _ = try backend.start()
            XCTFail("Expected normalized backend start failure", file: file, line: line)
        } catch {
            XCTAssertEqual(
                error as? BackendError,
                expected,
                "Expected normalized backend start failure",
                file: file,
                line: line
            )
        }
    }
}

private final class NativeHostFixture {
    enum Mode {
        case valid
        case noHandshake
        case oversizedHandshake
        case eofBeforeLineFeed
        case extraStdout
        case extraStdoutAfterRelease
        case exitBeforeHandshake
        case exitAfterRelease
        case ignoresShutdown
        case handshakeAfterRelease
        case stderrFlood
    }

    let directoryURL: URL
    let executableURL: URL
    private let argumentsURL: URL
    private let launchesURL: URL
    private let controlEOFURL: URL
    private let pidURL: URL
    private let readyURL: URL
    private let releaseURL: URL
    private let termURL: URL

    init(mode: Mode) throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("dotsync-native-fixture-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: false
        )
        directoryURL = directory
        executableURL = directory.appendingPathComponent("native-host-fixture")
        argumentsURL = directory.appendingPathComponent("arguments-ok")
        launchesURL = directory.appendingPathComponent("launches")
        controlEOFURL = directory.appendingPathComponent("control-eof")
        pidURL = directory.appendingPathComponent("pid")
        readyURL = directory.appendingPathComponent("ready")
        releaseURL = directory.appendingPathComponent("release")
        termURL = directory.appendingPathComponent("term")

        try Data(script(for: mode).utf8).write(
            to: executableURL,
            options: .atomic
        )
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: executableURL.path
        )
    }

    var waitedForControlEOF: Bool {
        FileManager.default.fileExists(atPath: controlEOFURL.path)
    }

    var receivedTerm: Bool {
        FileManager.default.fileExists(atPath: termURL.path)
    }

    var launchCount: Int {
        guard let data = try? Data(contentsOf: launchesURL),
              let source = String(data: data, encoding: .utf8)
        else { return 0 }
        return source.split(separator: "\n").count
    }

    var isRunning: Bool {
        guard let pid = processID else { return false }
        return kill(pid, 0) == 0
    }

    func waitForArguments(timeout: Duration) -> Bool {
        waitForFile(argumentsURL, timeout: timeout)
    }

    func waitForControlEOF(timeout: Duration) -> Bool {
        waitForFile(controlEOFURL, timeout: timeout)
    }

    func waitForReady(timeout: Duration) -> Bool {
        waitForFile(readyURL, timeout: timeout)
    }

    func waitForTerm(timeout: Duration) -> Bool {
        waitForFile(termURL, timeout: timeout)
    }

    func release() throws {
        try Data().write(to: releaseURL, options: .atomic)
    }

    func waitUntilStopped(timeout: Duration) -> Bool {
        waitUntil(timeout: timeout) { !self.isRunning }
    }

    func cleanup() {
        if let pid = processID, kill(pid, 0) == 0 {
            _ = kill(pid, SIGKILL)
        }
        try? FileManager.default.removeItem(at: directoryURL)
    }

    private var processID: pid_t? {
        guard let data = try? Data(contentsOf: pidURL),
              let source = String(data: data, encoding: .utf8),
              let pid = Int32(source)
        else { return nil }
        return pid
    }

    private func waitForFile(_ url: URL, timeout: Duration) -> Bool {
        waitUntil(timeout: timeout) {
            FileManager.default.fileExists(atPath: url.path)
        }
    }

    private func waitUntil(timeout: Duration, condition: () -> Bool) -> Bool {
        let deadline = DispatchTime.now() + timeout.dispatchInterval
        while DispatchTime.now() < deadline {
            if condition() { return true }
            usleep(10_000)
        }
        return condition()
    }

    private func script(for mode: Mode) -> String {
        let handshake = #"{"schema_version":1,"origin":"http://127.0.0.1:49152","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}"#
        let preamble = """
        #!/bin/sh
        printf '%s' "$$" > '\(pidURL.path)'
        printf 'x\\n' >> '\(launchesURL.path)'
        if [ "$#" -eq 2 ] && [ "$1" = "ui" ] && [ "$2" = "--native-host" ]; then
            : > '\(argumentsURL.path)'
        fi
        """ + "\n"
        switch mode {
        case .valid:
            return preamble + """
            printf '%s\\n' '\(handshake)'
            IFS= read -r ignored
            : > '\(controlEOFURL.path)'
            """
        case .noHandshake:
            return preamble + """
            while :; do :; done
            """
        case .oversizedHandshake:
            return preamble + """
            printf '%s\\n' '\(String(repeating: "A", count: 4_097))'
            IFS= read -r ignored
            """
        case .eofBeforeLineFeed:
            return preamble + """
            printf 'partial'
            exec 1>&-
            IFS= read -r ignored
            """
        case .extraStdout:
            return preamble + """
            printf '%s\\nX' '\(handshake)'
            IFS= read -r ignored
            """
        case .extraStdoutAfterRelease:
            return preamble + """
            printf '%s\\n' '\(handshake)'
            while [ ! -e '\(releaseURL.path)' ]; do :; done
            printf 'X'
            IFS= read -r ignored
            """
        case .exitBeforeHandshake:
            return preamble + """
            exit 7
            """
        case .exitAfterRelease:
            return preamble + """
            printf '%s\\n' '\(handshake)'
            while [ ! -e '\(releaseURL.path)' ]; do :; done
            exit 9
            """
        case .ignoresShutdown:
            return preamble + """
            trap ': > '\''\(termURL.path)'\''' TERM
            printf '%s\\n' '\(handshake)'
            while :; do :; done
            """
        case .handshakeAfterRelease:
            return preamble + """
            : > '\(readyURL.path)'
            while [ ! -e '\(releaseURL.path)' ]; do :; done
            printf '%s\\n' '\(handshake)'
            IFS= read -r ignored
            : > '\(controlEOFURL.path)'
            """
        case .stderrFlood:
            return preamble + """
            count=0
            while [ "$count" -lt 20000 ]; do
                printf 'discarded stderr\\n' >&2
                count=$((count + 1))
            done
            printf '%s\\n' '\(handshake)'
            IFS= read -r ignored
            : > '\(controlEOFURL.path)'
            """
        }
    }
}

private final class FirstWaitBarrierSystem: BackendProcessSystem,
    @unchecked Sendable {
    let waitEntered = DispatchSemaphore(value: 0)
    let allowWait = DispatchSemaphore(value: 0)
    private let base = FoundationBackendProcessSystem()
    private let lock = NSLock()
    private var blockedFirstWait = false

    func isRunning(_ process: Process) -> Bool {
        base.isRunning(process)
    }

    func waitForExit(
        _ process: Process,
        signal: DispatchSemaphore,
        timeout: Duration
    ) -> Bool {
        lock.lock()
        let shouldBlock = !blockedFirstWait
        blockedFirstWait = true
        lock.unlock()
        if shouldBlock {
            waitEntered.signal()
            allowWait.wait()
        }
        return base.waitForExit(process, signal: signal, timeout: timeout)
    }

    func sendTerminate(_ process: Process) -> Bool {
        base.sendTerminate(process)
    }

    func sendKill(_ process: Process) -> Bool {
        base.sendKill(process)
    }
}

private final class FailFirstKillSystem: BackendProcessSystem,
    @unchecked Sendable {
    let retryEntered = DispatchSemaphore(value: 0)
    let allowRetry = DispatchSemaphore(value: 0)
    private let base = FoundationBackendProcessSystem()
    private let lock = NSLock()
    private var killAttempts = 0

    func isRunning(_ process: Process) -> Bool {
        base.isRunning(process)
    }

    func waitForExit(
        _ process: Process,
        signal: DispatchSemaphore,
        timeout: Duration
    ) -> Bool {
        base.waitForExit(process, signal: signal, timeout: timeout)
    }

    func sendTerminate(_ process: Process) -> Bool {
        base.sendTerminate(process)
    }

    func sendKill(_ process: Process) -> Bool {
        lock.lock()
        killAttempts += 1
        let attempt = killAttempts
        lock.unlock()
        if attempt == 1 {
            return false
        }
        if attempt == 2 {
            retryEntered.signal()
            allowRetry.wait()
        }
        return base.sendKill(process)
    }
}

private final class LockedBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: Value

    init(_ value: Value) {
        storage = value
    }

    var value: Value {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func withValue(_ operation: (inout Value) -> Void) {
        lock.lock()
        defer { lock.unlock() }
        operation(&storage)
    }
}

private extension Duration {
    var dispatchInterval: DispatchTimeInterval {
        let components = self.components
        let seconds = components.seconds
        let nanoseconds = components.attoseconds / 1_000_000_000
        if seconds > Int64(Int.max) { return .never }
        return .nanoseconds(Int(seconds) * 1_000_000_000 + Int(nanoseconds))
    }
}
