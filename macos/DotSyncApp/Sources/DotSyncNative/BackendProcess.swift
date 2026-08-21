import Foundation

public struct BackendSession: Equatable, Sendable {
    public let origin: LocalOrigin
}

struct BackendProcessTestHooks: Sendable {
    let beforeTerminationRecord: @Sendable () -> Void

    init(
        beforeTerminationRecord: @escaping @Sendable () -> Void = {}
    ) {
        self.beforeTerminationRecord = beforeTerminationRecord
    }
}

protocol BackendProcessSystem: Sendable {
    func isRunning(_ process: Process) -> Bool
    func waitForExit(
        _ process: Process,
        signal: DispatchSemaphore,
        timeout: Duration
    ) -> Bool
    func sendTerminate(_ process: Process) -> Bool
    func sendKill(_ process: Process) -> Bool
}

struct FoundationBackendProcessSystem: BackendProcessSystem {
    func isRunning(_ process: Process) -> Bool {
        process.isRunning
    }

    func waitForExit(
        _ process: Process,
        signal: DispatchSemaphore,
        timeout: Duration
    ) -> Bool {
        if !process.isRunning { return true }
        let result = signal.wait(timeout: .now() + timeout.dispatchInterval)
        return result == .success || !process.isRunning
    }

    func sendTerminate(_ process: Process) -> Bool {
        sendSignal(SIGTERM, to: process)
    }

    func sendKill(_ process: Process) -> Bool {
        sendSignal(SIGKILL, to: process)
    }

    private func sendSignal(_ signal: Int32, to process: Process) -> Bool {
        if !process.isRunning { return true }
        if kill(process.processIdentifier, signal) == 0 { return true }
        return errno == ESRCH && !process.isRunning
    }
}

public final class BackendProcess: @unchecked Sendable {
    private let core: BackendProcessCore

    public init(
        resolver: BackendExecutableResolver = .init(),
        testOverride: URL? = nil,
        handshakeTimeout: Duration = .seconds(5),
        onUnexpectedExit: @escaping @Sendable (BackendError) -> Void = { _ in }
    ) {
        core = BackendProcessCore(
            resolver: resolver,
            testOverride: testOverride,
            handshakeTimeout: handshakeTimeout,
            onUnexpectedExit: onUnexpectedExit,
            system: FoundationBackendProcessSystem(),
            testHooks: BackendProcessTestHooks()
        )
    }

    init(
        resolver: BackendExecutableResolver = .init(),
        testOverride: URL? = nil,
        handshakeTimeout: Duration = .seconds(5),
        onUnexpectedExit: @escaping @Sendable (BackendError) -> Void = { _ in },
        system: any BackendProcessSystem,
        testHooks: BackendProcessTestHooks = BackendProcessTestHooks()
    ) {
        core = BackendProcessCore(
            resolver: resolver,
            testOverride: testOverride,
            handshakeTimeout: handshakeTimeout,
            onUnexpectedExit: onUnexpectedExit,
            system: system,
            testHooks: testHooks
        )
    }

    deinit {
        core.requestStopForDeinit()
    }

    public func start() throws -> BackendSession {
        try core.start()
    }

    public func stop() async throws {
        try await core.stop()
    }
}

private final class BackendProcessCore: @unchecked Sendable {
    private typealias StopContinuation = CheckedContinuation<Void, Error>

    private enum LifecycleState {
        case idle
        case starting(StartOperation)
        case running(OwnedProcess)
        case stopping(StopOperation)
    }

    private enum StopReason {
        case requested
        case protocolViolation
        case failedStart
    }

    private final class StartOperation {
        var process: Process?
        var exitObserved = false
        var stopRequested = false
        var stopWaiters: [StopContinuation] = []
    }

    private final class StopOperation {
        let owner: OwnedProcess
        let reason: StopReason
        var exitObserved: Bool
        var unexpectedExitReported: Bool
        var waiters: [StopContinuation]

        init(
            owner: OwnedProcess,
            reason: StopReason,
            exitObserved: Bool = false,
            unexpectedExitReported: Bool = false,
            waiters: [StopContinuation] = []
        ) {
            self.owner = owner
            self.reason = reason
            self.exitObserved = exitObserved
            self.unexpectedExitReported = unexpectedExitReported
            self.waiters = waiters
        }

        func reserveUnexpectedExitReportForPriorExit() -> Bool {
            guard exitObserved, !unexpectedExitReported else { return false }
            guard case .requested = reason else { return false }
            unexpectedExitReported = true
            return true
        }
    }

    private let lock = NSLock()
    private let executor = DispatchQueue(label: "com.dotsync.native.backend-lifecycle")
    private let resolver: BackendExecutableResolver
    private let testOverride: URL?
    private let handshakeTimeout: Duration
    private let onUnexpectedExit: @Sendable (BackendError) -> Void
    private let system: any BackendProcessSystem
    private let testHooks: BackendProcessTestHooks
    private var state: LifecycleState = .idle

    init(
        resolver: BackendExecutableResolver,
        testOverride: URL?,
        handshakeTimeout: Duration,
        onUnexpectedExit: @escaping @Sendable (BackendError) -> Void,
        system: any BackendProcessSystem,
        testHooks: BackendProcessTestHooks
    ) {
        self.resolver = resolver
        self.testOverride = testOverride
        self.handshakeTimeout = handshakeTimeout
        self.onUnexpectedExit = onUnexpectedExit
        self.system = system
        self.testHooks = testHooks
    }

    func start() throws -> BackendSession {
        let attempt = StartOperation()
        lock.lock()
        guard case .idle = state else {
            lock.unlock()
            throw BackendError.backendStartFailed
        }
        state = .starting(attempt)
        lock.unlock()

        let executable: URL
        do {
            executable = try resolver.resolve(testOverride: testOverride)
        } catch {
            finishStartWithoutChild(attempt)
            throw normalizeResolverFailure(error)
        }

        let control = Pipe()
        let handshake = Pipe()
        let child = Process()
        let exited = DispatchSemaphore(value: 0)
        child.executableURL = executable
        child.arguments = ["ui", "--native-host"]
        child.environment = sanitizedBackendEnvironment(
            from: ProcessInfo.processInfo.environment
        )
        child.currentDirectoryURL = URL(fileURLWithPath: "/", isDirectory: true)
        child.standardInput = control
        child.standardOutput = handshake
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] terminated in
            exited.signal()
            self?.testHooks.beforeTerminationRecord()
            self?.recordTermination(of: terminated)
        }

        do {
            try child.run()
        } catch {
            closeAllPipeEnds(control: control, handshake: handshake)
            finishStartWithoutChild(attempt)
            throw BackendError.backendStartFailed
        }

        lock.lock()
        attempt.process = child
        lock.unlock()
        control.fileHandleForReading.closeFile()
        handshake.fileHandleForWriting.closeFile()

        let decoded: LaunchHandshake
        do {
            let line = try readHandshakeLine(
                handshake.fileHandleForReading,
                maximumBytes: 4_096,
                timeout: handshakeTimeout
            )
            decoded = try LaunchHandshake.decode(line)
        } catch HandshakeReadFailure.emptyEOF {
            let exitedBeforeHandshake = system.waitForExit(
                child,
                signal: exited,
                timeout: .milliseconds(50)
            )
            handshake.fileHandleForReading.closeFile()
            let owner = OwnedProcess(
                process: child,
                controlWriter: control.fileHandleForWriting,
                exitSignal: exited,
                monitor: nil
            )
            retainOrFinishFailedStart(attempt, owner: owner)
            throw exitedBeforeHandshake
                ? BackendError.backendExited
                : BackendError.backendProtocolError
        } catch {
            handshake.fileHandleForReading.closeFile()
            let owner = OwnedProcess(
                process: child,
                controlWriter: control.fileHandleForWriting,
                exitSignal: exited,
                monitor: nil
            )
            retainOrFinishFailedStart(attempt, owner: owner)
            throw normalizeProtocolFailure(error)
        }

        let monitor = ProtocolSilenceMonitor(
            handle: handshake.fileHandleForReading,
            onByte: { [weak self, child] in
                self?.recordProtocolViolation(of: child)
            }
        )
        let owner = OwnedProcess(
            process: child,
            controlWriter: control.fileHandleForWriting,
            exitSignal: exited,
            monitor: monitor
        )
        let publishResult = publishStartedOwner(owner, for: attempt)
        switch publishResult {
        case .running:
            monitor.start()
        case let .stopping(operation):
            monitor.start()
            scheduleInitialShutdown(operation)
        case .exited:
            retainOrFinishFailedStart(attempt, owner: owner)
            throw BackendError.backendExited
        }
        return BackendSession(origin: decoded.origin)
    }

    func stop() async throws {
        try await withCheckedThrowingContinuation { continuation in
            requestStop(continuation: continuation)
        }
    }

    func requestStopForDeinit() {
        requestStop(continuation: nil)
    }

    private enum PublishResult {
        case running
        case stopping(StopOperation)
        case exited
    }

    private func publishStartedOwner(
        _ owner: OwnedProcess,
        for attempt: StartOperation
    ) -> PublishResult {
        lock.lock()
        defer { lock.unlock() }
        guard case let .starting(current) = state, current === attempt
        else { return .exited }
        if attempt.exitObserved || !system.isRunning(owner.process) {
            return .exited
        }
        if attempt.stopRequested {
            let operation = StopOperation(
                owner: owner,
                reason: .requested,
                waiters: attempt.stopWaiters
            )
            state = .stopping(operation)
            return .stopping(operation)
        }
        state = .running(owner)
        return .running
    }

    private func requestStop(continuation: StopContinuation?) {
        var operationToSchedule: StopOperation?
        var callback: BackendError?
        var immediateContinuation: StopContinuation?
        lock.lock()
        switch state {
        case .idle:
            immediateContinuation = continuation
        case let .starting(attempt):
            attempt.stopRequested = true
            if let continuation {
                attempt.stopWaiters.append(continuation)
            }
        case let .running(owner):
            let exitedBeforeIntent = !system.isRunning(owner.process)
            let operation = StopOperation(
                owner: owner,
                reason: .requested,
                exitObserved: exitedBeforeIntent,
                waiters: continuation.map { [$0] } ?? []
            )
            state = .stopping(operation)
            operationToSchedule = operation
            if operation.reserveUnexpectedExitReportForPriorExit() {
                callback = .backendExited
            }
        case let .stopping(operation):
            if let continuation {
                operation.waiters.append(continuation)
            }
        }
        lock.unlock()

        immediateContinuation?.resume()
        if let callback {
            onUnexpectedExit(callback)
        }
        if let operationToSchedule {
            scheduleInitialShutdown(operationToSchedule)
        }
    }

    private func recordTermination(of process: Process) {
        var callback: BackendError?
        var ownerToClose: OwnedProcess?
        lock.lock()
        switch state {
        case .idle:
            break
        case let .starting(attempt):
            if attempt.process === process {
                attempt.exitObserved = true
            }
        case let .running(owner):
            if owner.process === process {
                state = .idle
                ownerToClose = owner
                callback = .backendExited
            }
        case let .stopping(operation):
            if operation.owner.process === process {
                operation.exitObserved = true
            }
        }
        lock.unlock()

        ownerToClose?.closeResources()
        if let callback {
            onUnexpectedExit(callback)
        }
    }

    private func recordProtocolViolation(of process: Process) {
        var operationToSchedule: StopOperation?
        lock.lock()
        if case let .running(owner) = state, owner.process === process {
            let operation = StopOperation(
                owner: owner,
                reason: .protocolViolation,
                unexpectedExitReported: true
            )
            state = .stopping(operation)
            operationToSchedule = operation
        }
        lock.unlock()

        guard let operationToSchedule else { return }
        onUnexpectedExit(.backendProtocolError)
        scheduleInitialShutdown(operationToSchedule)
    }

    private func finishStartWithoutChild(_ attempt: StartOperation) {
        let waiters: [StopContinuation]
        lock.lock()
        if case let .starting(current) = state, current === attempt {
            state = .idle
            waiters = attempt.stopWaiters
        } else {
            waiters = []
        }
        lock.unlock()
        resume(waiters, with: .success(()))
    }

    private func retainOrFinishFailedStart(
        _ attempt: StartOperation,
        owner: OwnedProcess
    ) {
        owner.closeResources()
        if performFailedStartShutdown(owner) {
            finishStartWithoutChild(attempt)
            return
        }

        let operation: StopOperation?
        lock.lock()
        if case let .starting(current) = state, current === attempt {
            let retained = StopOperation(
                owner: owner,
                reason: .failedStart,
                exitObserved: attempt.exitObserved,
                waiters: attempt.stopWaiters
            )
            state = .stopping(retained)
            operation = retained
        } else {
            operation = nil
        }
        lock.unlock()
        if let operation {
            deliverInitialFailure(operation)
            scheduleRetainedReap(operation)
        }
    }

    private func scheduleInitialShutdown(_ operation: StopOperation) {
        executor.async { [self] in
            operation.owner.closeResources()
            if performRequestedShutdown(operation.owner) {
                finishStop(operation)
            } else {
                deliverInitialFailure(operation)
                scheduleRetainedReap(operation)
            }
        }
    }

    private func scheduleRetainedReap(_ operation: StopOperation) {
        executor.asyncAfter(deadline: .now() + .milliseconds(100)) { [self] in
            guard isCurrent(operation) else { return }
            if system.waitForExit(
                operation.owner.process,
                signal: operation.owner.exitSignal,
                timeout: .milliseconds(100)
            ) {
                finishStop(operation)
                return
            }
            let signalSent = system.sendKill(operation.owner.process)
            if signalSent,
               system.waitForExit(
                   operation.owner.process,
                   signal: operation.owner.exitSignal,
                   timeout: .seconds(1)
               ) {
                finishStop(operation)
                return
            }
            scheduleRetainedReap(operation)
        }
    }

    private func performRequestedShutdown(_ owner: OwnedProcess) -> Bool {
        if system.waitForExit(
            owner.process,
            signal: owner.exitSignal,
            timeout: .seconds(3)
        ) {
            return true
        }
        let termSent = system.sendTerminate(owner.process)
        if termSent,
           system.waitForExit(
               owner.process,
               signal: owner.exitSignal,
               timeout: .seconds(1)
           ) {
            return true
        }
        if !system.isRunning(owner.process) { return true }
        guard system.sendKill(owner.process) else { return false }
        return system.waitForExit(
            owner.process,
            signal: owner.exitSignal,
            timeout: .seconds(1)
        )
    }

    private func performFailedStartShutdown(_ owner: OwnedProcess) -> Bool {
        if system.waitForExit(
            owner.process,
            signal: owner.exitSignal,
            timeout: .milliseconds(100)
        ) {
            return true
        }
        let termSent = system.sendTerminate(owner.process)
        if termSent,
           system.waitForExit(
               owner.process,
               signal: owner.exitSignal,
               timeout: .seconds(1)
           ) {
            return true
        }
        if !system.isRunning(owner.process) { return true }
        guard system.sendKill(owner.process) else { return false }
        return system.waitForExit(
            owner.process,
            signal: owner.exitSignal,
            timeout: .seconds(1)
        )
    }

    private func deliverInitialFailure(_ operation: StopOperation) {
        let waiters: [StopContinuation]
        lock.lock()
        if case let .stopping(current) = state, current === operation {
            waiters = operation.waiters
            operation.waiters.removeAll()
        } else {
            waiters = []
        }
        lock.unlock()
        resume(waiters, with: .failure(BackendError.backendExited))
    }

    private func finishStop(_ operation: StopOperation) {
        let waiters: [StopContinuation]
        lock.lock()
        if case let .stopping(current) = state, current === operation {
            state = .idle
            waiters = operation.waiters
            operation.waiters.removeAll()
        } else {
            waiters = []
        }
        lock.unlock()
        resume(waiters, with: .success(()))
    }

    private func isCurrent(_ operation: StopOperation) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard case let .stopping(current) = state else { return false }
        return current === operation
    }

    private func resume(
        _ waiters: [StopContinuation],
        with result: Result<Void, Error>
    ) {
        for waiter in waiters {
            waiter.resume(with: result)
        }
    }
}

func sanitizedBackendEnvironment(
    from source: [String: String]
) -> [String: String] {
    // The Formula launcher needs only user/temp, locale, and macOS XPC context.
    let allowedKeys = [
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
        "XPC_FLAGS",
        "XPC_SERVICE_NAME",
    ]
    return Dictionary(
        uniqueKeysWithValues: allowedKeys.compactMap { key in
            source[key].map { (key, $0) }
        }
    )
}

private final class OwnedProcess: @unchecked Sendable {
    let process: Process
    let exitSignal: DispatchSemaphore
    private let lock = NSLock()
    private var controlWriter: FileHandle?
    private var monitor: ProtocolSilenceMonitor?

    init(
        process: Process,
        controlWriter: FileHandle,
        exitSignal: DispatchSemaphore,
        monitor: ProtocolSilenceMonitor?
    ) {
        self.process = process
        self.controlWriter = controlWriter
        self.exitSignal = exitSignal
        self.monitor = monitor
    }

    func closeResources() {
        let writer: FileHandle?
        let ownedMonitor: ProtocolSilenceMonitor?
        lock.lock()
        writer = controlWriter
        controlWriter = nil
        ownedMonitor = monitor
        monitor = nil
        lock.unlock()
        ownedMonitor?.stop()
        writer?.closeFile()
    }
}

private enum HandshakeReadFailure: Error {
    case emptyEOF
    case protocolViolation
}

private func readHandshakeLine(
    _ handle: FileHandle,
    maximumBytes: Int,
    timeout: Duration
) throws -> Data {
    guard maximumBytes > 0 else {
        throw HandshakeReadFailure.protocolViolation
    }
    let timeoutNanoseconds = timeout.nonnegativeNanoseconds
    let now = DispatchTime.now().uptimeNanoseconds
    let deadline = now.addingReportingOverflow(timeoutNanoseconds)
    let deadlineNanoseconds = deadline.overflow ? UInt64.max : deadline.partialValue
    var line: [UInt8] = []
    var descriptor = pollfd(
        fd: handle.fileDescriptor,
        events: Int16(POLLIN | POLLHUP),
        revents: 0
    )

    while true {
        let current = DispatchTime.now().uptimeNanoseconds
        guard current < deadlineNanoseconds else {
            throw HandshakeReadFailure.protocolViolation
        }
        let remaining = deadlineNanoseconds - current
        let roundedMilliseconds = remaining / 1_000_000
            + (remaining % 1_000_000 == 0 ? 0 : 1)
        let timeoutMilliseconds = min(
            UInt64(Int32.max),
            max(1, roundedMilliseconds)
        )
        descriptor.revents = 0
        let pollResult = poll(&descriptor, 1, Int32(timeoutMilliseconds))
        if pollResult == 0 {
            throw HandshakeReadFailure.protocolViolation
        }
        if pollResult < 0 {
            if errno == EINTR { continue }
            throw HandshakeReadFailure.protocolViolation
        }

        var chunk = [UInt8](repeating: 0, count: maximumBytes + 2)
        let bytesRead = chunk.withUnsafeMutableBytes { buffer in
            read(handle.fileDescriptor, buffer.baseAddress, buffer.count)
        }
        if bytesRead == 0 {
            if line.isEmpty { throw HandshakeReadFailure.emptyEOF }
            throw HandshakeReadFailure.protocolViolation
        }
        if bytesRead < 0 {
            if errno == EINTR { continue }
            throw HandshakeReadFailure.protocolViolation
        }
        line.append(contentsOf: chunk.prefix(bytesRead))
        if let lineFeed = line.firstIndex(of: 0x0a) {
            guard lineFeed <= maximumBytes,
                  lineFeed == line.count - 1
            else { throw HandshakeReadFailure.protocolViolation }
            return Data(line[..<lineFeed])
        }
        guard line.count <= maximumBytes
        else { throw HandshakeReadFailure.protocolViolation }
    }
}

private func closeAllPipeEnds(control: Pipe, handshake: Pipe) {
    control.fileHandleForReading.closeFile()
    control.fileHandleForWriting.closeFile()
    handshake.fileHandleForReading.closeFile()
    handshake.fileHandleForWriting.closeFile()
}

private func normalizeResolverFailure(_ error: Error) -> BackendError {
    if error as? BackendError == .backendNotFound {
        return .backendNotFound
    }
    return .backendStartFailed
}

private func normalizeProtocolFailure(_ error: Error) -> BackendError {
    if let backendError = error as? BackendError {
        return backendError
    }
    return .backendProtocolError
}

private final class ProtocolSilenceMonitor: @unchecked Sendable {
    private let lock = NSLock()
    private let source: DispatchSourceRead
    private var started = false
    private var stopped = false

    init(handle: FileHandle, onByte: @escaping @Sendable () -> Void) {
        source = DispatchSource.makeReadSource(
            fileDescriptor: handle.fileDescriptor,
            queue: DispatchQueue(label: "com.dotsync.native.stdout-monitor")
        )
        source.setEventHandler { [weak self] in
            guard let self else { return }
            var byte: UInt8 = 0
            let count = read(handle.fileDescriptor, &byte, 1)
            if count > 0 {
                onByte()
                self.stop()
            } else if count == 0 || errno != EINTR {
                self.stop()
            }
        }
        source.setCancelHandler {
            handle.closeFile()
        }
    }

    func start() {
        lock.lock()
        guard !started, !stopped else {
            lock.unlock()
            return
        }
        started = true
        lock.unlock()
        source.resume()
    }

    func stop() {
        lock.lock()
        guard !stopped else {
            lock.unlock()
            return
        }
        stopped = true
        let needsResume = !started
        started = true
        lock.unlock()
        if needsResume { source.resume() }
        source.cancel()
    }
}

private extension Duration {
    var nonnegativeNanoseconds: UInt64 {
        let components = self.components
        guard components.seconds >= 0, components.attoseconds >= 0 else { return 0 }
        let seconds = UInt64(components.seconds)
        let nanoseconds = UInt64(components.attoseconds / 1_000_000_000)
        let secondsAsNanoseconds = seconds.multipliedReportingOverflow(by: 1_000_000_000)
        guard !secondsAsNanoseconds.overflow else { return UInt64.max }
        return secondsAsNanoseconds.partialValue.addingReportingOverflow(nanoseconds).overflow
            ? UInt64.max
            : secondsAsNanoseconds.partialValue + nanoseconds
    }

    var dispatchInterval: DispatchTimeInterval {
        let nanoseconds = nonnegativeNanoseconds
        guard nanoseconds <= UInt64(Int.max) else { return .never }
        return .nanoseconds(Int(nanoseconds))
    }
}
