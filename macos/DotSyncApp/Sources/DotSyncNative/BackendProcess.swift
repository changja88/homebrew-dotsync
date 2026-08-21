import Darwin
import Foundation

public struct BackendSession: Equatable, Sendable {
    public let origin: LocalOrigin
}

public final class BackendProcess: @unchecked Sendable {
    private let lock = NSLock()
    private let resolver: BackendExecutableResolver
    private let testOverride: URL?
    private let handshakeTimeout: Duration
    private let onUnexpectedExit: @Sendable (BackendError) -> Void
    private var process: Process?
    private var controlWriter: FileHandle?
    private var exitSignal: DispatchSemaphore?
    private var protocolMonitor: ProtocolSilenceMonitor?

    public init(
        resolver: BackendExecutableResolver = .init(),
        testOverride: URL? = nil,
        handshakeTimeout: Duration = .seconds(5),
        onUnexpectedExit: @escaping @Sendable (BackendError) -> Void = { _ in }
    ) {
        self.resolver = resolver
        self.testOverride = testOverride
        self.handshakeTimeout = handshakeTimeout
        self.onUnexpectedExit = onUnexpectedExit
    }

    deinit {
        stop()
    }

    public func start() throws -> BackendSession {
        lock.lock()
        defer { lock.unlock() }
        guard process == nil else {
            throw BackendError.backendStartFailed
        }

        let executable = try resolver.resolve(testOverride: testOverride)
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
            self?.handleTermination(of: terminated)
        }

        do {
            try child.run()
        } catch {
            control.fileHandleForReading.closeFile()
            control.fileHandleForWriting.closeFile()
            handshake.fileHandleForReading.closeFile()
            handshake.fileHandleForWriting.closeFile()
            throw BackendError.backendStartFailed
        }

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
            let exitedBeforeHandshake = waitForExit(
                child,
                signal: exited,
                timeout: .milliseconds(50)
            )
            control.fileHandleForWriting.closeFile()
            handshake.fileHandleForReading.closeFile()
            terminateFailedStart(child, exitSignal: exited)
            throw exitedBeforeHandshake
                ? BackendError.backendExited
                : BackendError.backendProtocolError
        } catch {
            control.fileHandleForWriting.closeFile()
            handshake.fileHandleForReading.closeFile()
            terminateFailedStart(child, exitSignal: exited)
            throw normalizeProtocolFailure(error)
        }

        let monitor = ProtocolSilenceMonitor(
            handle: handshake.fileHandleForReading,
            onByte: { [weak self, child] in
                self?.handleProtocolViolation(of: child)
            }
        )
        process = child
        controlWriter = control.fileHandleForWriting
        exitSignal = exited
        protocolMonitor = monitor
        monitor.start()

        guard child.isRunning else {
            process = nil
            controlWriter = nil
            exitSignal = nil
            protocolMonitor = nil
            monitor.stop()
            control.fileHandleForWriting.closeFile()
            terminateFailedStart(child, exitSignal: exited)
            throw BackendError.backendExited
        }

        return BackendSession(origin: decoded.origin)
    }

    public func stop() {
        guard let owned = detachOwnedProcess() else { return }
        owned.monitor.stop()
        owned.controlWriter.closeFile()
        if waitForExit(owned.process, signal: owned.exitSignal, timeout: .seconds(3)) {
            return
        }
        if owned.process.isRunning {
            owned.process.terminate()
        }
        if waitForExit(owned.process, signal: owned.exitSignal, timeout: .seconds(1)) {
            return
        }
        if owned.process.isRunning {
            _ = kill(owned.process.processIdentifier, SIGKILL)
        }
        _ = waitForExit(owned.process, signal: owned.exitSignal, timeout: .seconds(1))
    }

    private func handleTermination(of child: Process) {
        guard let owned = detachOwnedProcess(matching: child) else { return }
        owned.monitor.stop()
        owned.controlWriter.closeFile()
        onUnexpectedExit(.backendExited)
    }

    private func handleProtocolViolation(of child: Process) {
        guard let owned = detachOwnedProcess(matching: child) else { return }
        owned.monitor.stop()
        owned.controlWriter.closeFile()
        terminateFailedStart(owned.process, exitSignal: owned.exitSignal)
        onUnexpectedExit(.backendProtocolError)
    }

    private func detachOwnedProcess(matching expected: Process? = nil) -> OwnedProcess? {
        lock.lock()
        defer { lock.unlock() }
        guard let process,
              expected == nil || process === expected,
              let controlWriter,
              let exitSignal,
              let protocolMonitor
        else { return nil }
        self.process = nil
        self.controlWriter = nil
        self.exitSignal = nil
        self.protocolMonitor = nil
        return OwnedProcess(
            process: process,
            controlWriter: controlWriter,
            exitSignal: exitSignal,
            monitor: protocolMonitor
        )
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

private struct OwnedProcess {
    let process: Process
    let controlWriter: FileHandle
    let exitSignal: DispatchSemaphore
    let monitor: ProtocolSilenceMonitor
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
        let pollResult = Darwin.poll(&descriptor, 1, Int32(timeoutMilliseconds))
        if pollResult == 0 {
            throw HandshakeReadFailure.protocolViolation
        }
        if pollResult < 0 {
            if errno == EINTR { continue }
            throw HandshakeReadFailure.protocolViolation
        }

        var chunk = [UInt8](repeating: 0, count: maximumBytes + 2)
        let bytesRead = chunk.withUnsafeMutableBytes { buffer in
            Darwin.read(handle.fileDescriptor, buffer.baseAddress, buffer.count)
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

private func normalizeProtocolFailure(_ error: Error) -> BackendError {
    if let backendError = error as? BackendError {
        return backendError
    }
    return .backendProtocolError
}

private func terminateFailedStart(
    _ process: Process,
    exitSignal: DispatchSemaphore
) {
    if waitForExit(process, signal: exitSignal, timeout: .milliseconds(100)) {
        return
    }
    if process.isRunning {
        process.terminate()
    }
    if waitForExit(process, signal: exitSignal, timeout: .seconds(1)) {
        return
    }
    if process.isRunning {
        _ = kill(process.processIdentifier, SIGKILL)
    }
    _ = waitForExit(process, signal: exitSignal, timeout: .seconds(1))
}

private func waitForExit(
    _ process: Process,
    signal: DispatchSemaphore,
    timeout: Duration
) -> Bool {
    if !process.isRunning { return true }
    let result = signal.wait(timeout: .now() + timeout.dispatchInterval)
    return result == .success || !process.isRunning
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
            let count = Darwin.read(handle.fileDescriptor, &byte, 1)
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
