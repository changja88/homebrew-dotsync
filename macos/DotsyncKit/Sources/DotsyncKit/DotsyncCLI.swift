import Foundation

/// Runs `dotsync … --json` and decodes its one JSON answer.
public struct DotsyncCLI: Sendable {
    public let executable: URL
    public let environment: [String: String]
    /// Where `longRun` commands are registered, so the app can stop them when
    /// the Mac powers off. Tests give each CLI its own.
    let runningCommands: RunningCommands

    public init(executable: URL, environment: [String: String] = DotsyncCLI.defaultEnvironment(),
                runningCommands: RunningCommands = .shared) {
        self.executable = executable
        self.environment = environment
        self.runningCommands = runningCommands
    }

    /// dotsync from Homebrew, or the `cliPath` user default (development:
    /// `defaults write com.changja88.dotsync cliPath <path>`).
    public static func locate(defaults: UserDefaults = .standard,
                              fileManager: FileManager = .default) -> URL? {
        locate(configuredPath: defaults.string(forKey: "cliPath"), fileManager: fileManager)
    }

    static func locate(configuredPath: String?, fileManager: FileManager = .default) -> URL? {
        let candidates = [configuredPath, "/opt/homebrew/bin/dotsync", "/usr/local/bin/dotsync"]
        return candidates.compactMap { $0 }
            .first { fileManager.isExecutableFile(atPath: $0) }
            .map { URL(fileURLWithPath: $0) }
    }

    /// The app's environment with Homebrew first on PATH: apps start with a
    /// short PATH, and dotsync has to find `claude`.
    public static func defaultEnvironment(_ base: [String: String] = ProcessInfo.processInfo.environment)
        -> [String: String] {
        var environment = base
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (base["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        return environment
    }

    public func usage() async throws -> UsageReport {
        try decode(UsageReport.self, try await run("usage", stopsOnCancel: true))
    }

    public func use(_ name: String, overwriteUnsaved: Bool) async throws -> AccountInfo {
        try decode(AccountInfo.self, try await run("use", options: overwriteUnsaved ? ["--yes"] : [], positionals: [name]))
    }

    /// Waits for the browser. Cancelling the calling task sends SIGTERM, which
    /// dotsync turns into a clean cancel.
    public func login(_ name: String) async throws -> AccountInfo {
        try decode(AccountInfo.self, try await run("login", positionals: [name], stopsOnCancel: true))
    }

    public func rename(_ name: String, to label: String) async throws -> AccountInfo {
        try decode(AccountInfo.self, try await run("rename", positionals: [name, label]))
    }

    public func remove(_ name: String) async throws {
        _ = try await run("remove", positionals: [name])
    }

    /// `dotsync account <command> --json [options] -- <positionals>`. The `--`
    /// keeps a name or label that starts with "-" from reading as an option.
    /// Only a command that `stopsOnCancel` is stopped (SIGTERM) when the
    /// calling task is cancelled: `usage` only reads, and dotsync turns a
    /// stopped `login` into a clean cancel. The others write the Keychain and
    /// Claude's files one after another; they run to the end. The timeout is
    /// long because `login` waits for a browser.
    func run(_ command: String, options: [String] = [], positionals: [String] = [],
             stopsOnCancel: Bool = false) async throws -> Data {
        let arguments = ["account", command, "--json"] + options + (positionals.isEmpty ? [] : ["--"] + positionals)
        return try await runJSON(arguments, timeout: .seconds(600), stopsOnCancel: stopsOnCancel)
    }

    /// Runs `dotsync <arguments>` and returns its JSON answer. One that has
    /// not exited by `timeout` is stopped (SIGTERM) and reported as
    /// `timeout`. A `longRun` is registered with `runningCommands` while it
    /// runs. dotsync's progress lines (`dotsync-progress <done>/<total>[ <phase>]` on
    /// stderr) go to `progress` as they arrive; the other stderr lines are
    /// kept for the error message.
    func runJSON(_ arguments: [String], timeout: Duration, stopsOnCancel: Bool = false,
                 longRun: Bool = false, progress: (@Sendable (Int, Int, String?) -> Void)? = nil) async throws -> Data {
        let output: Output
        do {
            output = try await execute(arguments, stopsOnCancel: stopsOnCancel, timeout: timeout,
                                       registersAsLongRun: longRun, progress: progress)
        } catch let error as CLIError {
            throw error
        } catch {
            throw CLIError(code: "failed", message: "dotsync를 실행할 수 없어요: \(error.localizedDescription)")
        }
        if output.status == 0 {
            return output.stdout
        }
        if let envelope = try? UsageJSON.decoder().decode(CLIErrorEnvelope.self, from: output.stdout) {
            throw envelope.error
        }
        if stopsOnCancel && Task.isCancelled {
            // Stopped before it answered; there is no exit status to report.
            throw CLIError(code: "cancelled", message: "dotsync was stopped")
        }
        let lastLine = String(decoding: output.stderr, as: UTF8.self)
            .split(separator: "\n").last.map(String.init)
        throw CLIError(code: "failed", message: lastLine ?? "dotsync exited with \(output.status)")
    }

    func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try UsageJSON.decoder().decode(type, from: data)
        } catch {
            throw CLIError(code: "failed", message: "dotsync의 답을 읽을 수 없어요")
        }
    }

    struct Output {
        var status: Int32
        var stdout: Data
        var stderr: Data
    }

    func execute(_ arguments: [String], stopsOnCancel: Bool, timeout: Duration,
                 registersAsLongRun: Bool = false,
                 progress: (@Sendable (Int, Int, String?) -> Void)? = nil) async throws -> Output {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        let (exits, exited) = AsyncStream<Int32>.makeStream()
        process.terminationHandler = { finished in
            exited.yield(finished.terminationStatus)
            exited.finish()
        }
        try process.run()
        if registersAsLongRun { runningCommands.add(process) }
        defer { runningCommands.remove(process) }
        // Drain both pipes while dotsync runs so neither fills up and stalls it.
        let out = Task.detached { stdout.fileHandleForReading.readDataToEndOfFile() }
        let err = Task.detached { Self.readLines(of: stderr.fileHandleForReading, progress: progress) }
        // Its own task: in a cancelled one the loop would end at once, before
        // dotsync does.
        let waiting = Task {
            var status: Int32 = -1
            for await value in exits { status = value }
            return status
        }
        let deadline = Task {
            try await Task.sleep(for: timeout)
            process.terminate()
        }
        let status = await withTaskCancellationHandler {
            await waiting.value
        } onCancel: {
            if stopsOnCancel { process.terminate() }
        }
        deadline.cancel()
        if case .success = await deadline.result {
            // Not the pipes: a child dotsync started may still hold them open.
            throw CLIError(code: "timeout", message: "dotsync가 제때 끝나지 않았어요")
        }
        return Output(status: status, stdout: await out.value, stderr: await err.value)
    }

    /// Reads stderr to its end, reporting progress lines as they arrive.
    /// Returns the other lines.
    private static func readLines(of handle: FileHandle, progress: (@Sendable (Int, Int, String?) -> Void)?) -> Data {
        var lines = StderrLines()
        while case let chunk = handle.availableData, !chunk.isEmpty {
            for step in lines.feed(chunk) { progress?(step.done, step.total, step.phase) }
        }
        for step in lines.finish() { progress?(step.done, step.total, step.phase) }
        return lines.kept
    }
}
