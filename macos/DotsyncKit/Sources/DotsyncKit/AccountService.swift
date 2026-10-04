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
    /// Called after every write to usage.json — the app redraws the widget.
    let didSave: @Sendable () -> Void

    public init(cli: DotsyncCLI, store: UsageStore, didSave: @escaping @Sendable () -> Void = {}) {
        self.cli = cli
        self.store = store
        self.didSave = didSave
    }

    /// Homebrew's dotsync and the app group's usage.json; nil when either is missing.
    public static func standard(didSave: @escaping @Sendable () -> Void = {}) -> AccountService? {
        guard let executable = DotsyncCLI.locate(), let store = UsageStore.shared() else { return nil }
        return AccountService(cli: DotsyncCLI(executable: executable), store: store, didSave: didSave)
    }

    public func current() -> UsageFile {
        store.load() ?? .empty
    }

    /// Notes that a refresh is starting, so the widget says 조회 중….
    public func markRefreshing() {
        save(UsageMerge.refreshing(store.load(), since: Date()))
    }

    @discardableResult
    public func refresh() async -> UsageFile {
        let previous = store.load()
        if previous?.isRefreshing(at: Date()) != true {
            markRefreshing()
        }
        let file: UsageFile
        do {
            file = UsageMerge.merge(previous: previous, report: try await cli.usage())
        } catch {
            let failure = Self.cliError(error)
            if failure.code == "cancelled" {
                // A refresh stopped on purpose (its task was cancelled) failed
                // nothing: put back what was there.
                var unchanged = previous ?? .empty
                unchanged.refreshingSince = nil
                save(unchanged)
                return unchanged
            }
            file = UsageMerge.failed(previous: previous, message: failure.message)
        }
        save(file)
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
        save(UsageMerge.switched(current(), to: name))
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
            save(file)
            return .success(file)
        } catch {
            return .failure(Self.cliError(error))
        }
    }

    public func remove(_ name: String) async -> Result<UsageFile, CLIError> {
        do {
            try await cli.remove(name)
            let file = UsageMerge.removed(current(), name)
            save(file)
            return .success(file)
        } catch {
            return .failure(Self.cliError(error))
        }
    }

    /// Something went wrong outside a refresh; show it where the widget looks.
    public func report(failure message: String) {
        save(UsageMerge.failed(previous: store.load(), message: message))
    }

    private func save(_ file: UsageFile) {
        try? store.save(file)
        didSave()
    }

    static func cliError(_ error: Error) -> CLIError {
        error as? CLIError ?? CLIError(code: "failed", message: error.localizedDescription)
    }
}
