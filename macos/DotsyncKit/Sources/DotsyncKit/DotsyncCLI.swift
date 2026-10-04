import Foundation

/// Runs `dotsync account … --json` and decodes its one JSON answer.
public struct DotsyncCLI: Sendable {
    public let executable: URL
    public let environment: [String: String]

    public init(executable: URL, environment: [String: String] = DotsyncCLI.defaultEnvironment()) {
        self.executable = executable
        self.environment = environment
    }

    /// dotsync from Homebrew, or the `cliPath` user default (development:
    /// `defaults write com.changja88.dotsync cliPath <path>`).
    public static func locate(defaults: UserDefaults = .standard,
                              fileManager: FileManager = .default) -> URL? {
        let candidates = [defaults.string(forKey: "cliPath"), "/opt/homebrew/bin/dotsync", "/usr/local/bin/dotsync"]
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
    /// Claude's files one after another; they run to the end.
    func run(_ command: String, options: [String] = [], positionals: [String] = [],
             stopsOnCancel: Bool = false) async throws -> Data {
        let arguments = ["account", command, "--json"] + options + (positionals.isEmpty ? [] : ["--"] + positionals)
        let output: Output
        do {
            output = try await execute(arguments, stopsOnCancel: stopsOnCancel)
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

    func execute(_ arguments: [String], stopsOnCancel: Bool) async throws -> Output {
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
        // Drain both pipes while dotsync runs so neither fills up and stalls it.
        let out = Task.detached { stdout.fileHandleForReading.readDataToEndOfFile() }
        let err = Task.detached { stderr.fileHandleForReading.readDataToEndOfFile() }
        // Its own task: in a cancelled one the loop would end at once, before
        // dotsync does.
        let waiting = Task {
            var status: Int32 = -1
            for await value in exits { status = value }
            return status
        }
        let status = await withTaskCancellationHandler {
            await waiting.value
        } onCancel: {
            if stopsOnCancel { process.terminate() }
        }
        return Output(status: status, stdout: await out.value, stderr: await err.value)
    }
}
