import Foundation

public enum UseOutcome: Equatable, Sendable {
    case switched(UsageFile)
    /// The login Claude uses isn't saved; switching would drop it.
    case needsConfirmation(email: String?)
    case failed(CLIError)
}

/// What the window's and the widget's buttons do: run dotsync and keep
/// usage.json in step with it.
public struct AccountService: Sendable {
    public let cli: DotsyncCLI
    public let store: UsageStore

    public init(cli: DotsyncCLI, store: UsageStore) {
        self.cli = cli
        self.store = store
    }

    /// Homebrew's dotsync and the app group's usage.json; nil when either is missing.
    public static func standard() -> AccountService? {
        guard let executable = DotsyncCLI.locate(), let store = UsageStore.shared() else { return nil }
        return AccountService(cli: DotsyncCLI(executable: executable), store: store)
    }

    public func current() -> UsageFile {
        store.load() ?? .empty
    }

    @discardableResult
    public func refresh() async -> UsageFile {
        let previous = store.load()
        let file: UsageFile
        do {
            file = UsageMerge.merge(previous: previous, report: try await cli.usage())
        } catch {
            let failure = Self.cliError(error)
            // A refresh stopped on purpose (its task was cancelled) failed nothing.
            if failure.code == "cancelled" { return previous ?? .empty }
            file = UsageMerge.failed(previous: previous, message: failure.message)
        }
        try? store.save(file)
        return file
    }

    public func use(_ name: String, overwriteUnsaved: Bool = false) async -> UseOutcome {
        do {
            _ = try await cli.use(name, overwriteUnsaved: overwriteUnsaved)
        } catch {
            let failure = Self.cliError(error)
            if failure.code == "unsaved_login" {
                return .needsConfirmation(email: failure.email)
            }
            return .failed(failure)
        }
        try? store.save(UsageMerge.switched(current(), to: name))
        return .switched(await refresh())
    }

    public func login(_ name: String) async -> Result<AccountInfo, CLIError> {
        do {
            return .success(try await cli.login(name))
        } catch {
            return .failure(Self.cliError(error))
        }
    }

    public func rename(_ name: String, to label: String) async -> Result<UsageFile, CLIError> {
        do {
            let file = UsageMerge.renamed(current(), try await cli.rename(name, to: label))
            try? store.save(file)
            return .success(file)
        } catch {
            return .failure(Self.cliError(error))
        }
    }

    public func remove(_ name: String) async -> Result<UsageFile, CLIError> {
        do {
            try await cli.remove(name)
            let file = UsageMerge.removed(current(), name)
            try? store.save(file)
            return .success(file)
        } catch {
            return .failure(Self.cliError(error))
        }
    }

    /// Something went wrong outside a refresh; show it where the widget looks.
    public func report(failure message: String) {
        try? store.save(UsageMerge.failed(previous: store.load(), message: message))
    }

    static func cliError(_ error: Error) -> CLIError {
        error as? CLIError ?? CLIError(code: "failed", message: error.localizedDescription)
    }
}
