import AppKit
import Foundation
import SwiftUI
import WebKit
import DotSyncNative

protocol BackendControlling: Sendable {
    func startBackend() async throws -> BackendSession
    func stopBackend() async throws
}

extension BackendProcess: BackendControlling {
    func startBackend() async throws -> BackendSession {
        try await Task.detached(priority: .userInitiated) {
            try self.start()
        }.value
    }

    func stopBackend() async throws {
        try await stop()
    }
}

enum BackendRecoveryIssue: Equatable {
    case installationRequired
    case backendUnavailable
}

enum BackendRecoveryAction: Equatable {
    case retry
    case openInstallationHelp
    case quit
}

@MainActor
final class AppCoordinator: ObservableObject {
    typealias SummaryFetcherFactory = @Sendable (
        LocalOrigin
    ) -> any MenuSummaryFetching

    @Published private(set) var session: BackendSession?
    @Published private(set) var summary = MenuSummaryModel()
    @Published private(set) var managerDestination = LocalOrigin.Destination.overview
    @Published private(set) var managerHandoffs: [ManagerSyncHandoff] = []
    @Published private(set) var recoveryIssue: BackendRecoveryIssue?
    @Published private(set) var isManagerPresented = false
    @Published private(set) var isTerminated = false

    let processPool = WKProcessPool()
    let recoveryActions: [BackendRecoveryAction] = [
        .retry,
        .openInstallationHelp,
        .quit,
    ]

    var recoveryTitle: String {
        switch recoveryIssue {
        case .installationRequired:
            return "DotSync backend is not installed."
        case .backendUnavailable, .none:
            return "DotSync could not start its local backend."
        }
    }

    private let backend: any BackendControlling
    private let summaryFetcherFactory: SummaryFetcherFactory
    private let terminator: @MainActor () -> Void
    private let installationHelpOpener: @MainActor () -> Void
    private var windowOpener: @MainActor () -> Void
    private var startTask: Task<Result<BackendSession, BackendError>, Never>?
    private var retryTask: Task<UInt64, Never>?
    private var retrySequence: UInt64 = 0
    private var pollingTask: Task<Void, Never>?
    private var summaryPoller: MenuSummaryPoller?
    private var summaryOwner: UInt64 = 0
    private var summaryRequestOwner: UInt64 = 0
    private var handoffSequence: UInt64 = 0
    private var isActive = true
    private var isQuitting = false

    init(
        backend: any BackendControlling,
        summaryFetcherFactory: @escaping SummaryFetcherFactory = {
            _ in UnknownSummaryFetcher()
        },
        terminator: @escaping @MainActor () -> Void = {},
        installationHelpOpener: @escaping @MainActor () -> Void = {},
        windowOpener: @escaping @MainActor () -> Void = {}
    ) {
        self.backend = backend
        self.summaryFetcherFactory = summaryFetcherFactory
        self.terminator = terminator
        self.installationHelpOpener = installationHelpOpener
        self.windowOpener = windowOpener
    }

    static func production() -> AppCoordinator {
        let exitRelay = BackendExitRelay()
        let backend = BackendProcess { error in
            exitRelay.report(error)
        }
        let coordinator = AppCoordinator(
            backend: backend,
            summaryFetcherFactory: { origin in
                MenuSummaryClient(origin: origin)
            },
            terminator: {
                NSApplication.shared.terminate(nil)
            },
            installationHelpOpener: {
                guard let url = URL(
                    string: "https://github.com/changja88/homebrew-dotsync#installation"
                ) else { return }
                NSWorkspace.shared.open(url)
            }
        )
        exitRelay.attach(coordinator)
        return coordinator
    }

    func start() async {
        guard !isTerminated, !isQuitting, session == nil else { return }
        if let startTask {
            _ = await startTask.value
            return
        }

        let backend = self.backend
        let operation = Task<Result<BackendSession, BackendError>, Never> {
            do {
                return .success(try await backend.startBackend())
            } catch let error as BackendError {
                return .failure(error)
            } catch {
                return .failure(.backendStartFailed)
            }
        }
        startTask = operation
        let result = await operation.value
        startTask = nil
        guard !isQuitting, !isTerminated else { return }

        switch result {
        case let .success(session):
            self.session = session
            recoveryIssue = nil
            let poller = MenuSummaryPoller(
                fetcher: summaryFetcherFactory(session.origin)
            )
            summaryOwner &+= 1
            let owner = summaryOwner
            summaryPoller = poller
            if let ownedResult = await poller.poll(
                isActive: isActive,
                requestStarted: { self.beginSummaryRequest() }
            ) {
                await acceptSummary(
                    ownedResult.result,
                    from: poller,
                    owner: owner,
                    requestOwner: ownedResult.requestOwner
                )
            }
            startPollingIfNeeded()
        case let .failure(error):
            presentRecovery(for: error)
        }
    }

    func surfaceAppeared(_ surface: LocalOrigin.Surface) {
        guard session == nil, recoveryIssue == nil, !isQuitting else { return }
        Task { await start() }
    }

    func installWindowOpener(
        _ opener: @escaping @MainActor () -> Void
    ) {
        windowOpener = opener
    }

    func openManager(_ request: ManagerRequest) {
        managerDestination = request.destination
        switch request {
        case .destination:
            break
        case let .sync(direction):
            handoffSequence &+= 1
            managerHandoffs.append(
                ManagerSyncHandoff(
                    sequence: handoffSequence,
                    direction: direction
                )
            )
        }
        isManagerPresented = true
        windowOpener()
    }

    func acknowledgeManagerHandoff(sequence: UInt64) {
        guard managerHandoffs.first?.sequence == sequence else { return }
        managerHandoffs.removeFirst()
    }

    func managerDidClose() {
        isManagerPresented = false
    }

    func handle(_ command: NativeCommand) async {
        switch command {
        case let .openManager(request):
            openManager(request)
        case .refreshSummary:
            await reloadSummary()
        case .quitApp:
            await quit()
        }
    }

    func setActive(_ active: Bool) {
        guard isActive != active else { return }
        isActive = active
        if active {
            startPollingIfNeeded()
            Task { await pollSummaryIfDue() }
        } else {
            pollingTask?.cancel()
            pollingTask = nil
        }
    }

    func backendExited(_ error: BackendError) {
        pollingTask?.cancel()
        pollingTask = nil
        summaryOwner &+= 1
        summaryPoller = nil
        session = nil
        summary = MenuSummaryModel()
        presentRecovery(for: error)
    }

    func retry() async {
        guard !isTerminated, !isQuitting else { return }
        if let retryTask {
            _ = await retryTask.value
            return
        }

        retrySequence &+= 1
        let sequence = retrySequence
        let operation = Task { @MainActor in
            await self.performRetry()
            return sequence
        }
        retryTask = operation
        let completedSequence = await operation.value
        if completedSequence == sequence {
            retryTask = nil
        }
    }

    private func performRetry() async {
        guard !isTerminated, !isQuitting else { return }
        pollingTask?.cancel()
        pollingTask = nil
        do {
            try await backend.stopBackend()
        } catch {
            presentRecovery(for: .backendExited)
            return
        }
        session = nil
        summaryOwner &+= 1
        summaryPoller = nil
        recoveryIssue = nil
        await start()
    }

    func openInstallationHelp() {
        installationHelpOpener()
    }

    func quit() async {
        guard !isTerminated, !isQuitting else { return }
        isQuitting = true
        pollingTask?.cancel()
        pollingTask = nil
        do {
            try await backend.stopBackend()
            session = nil
            summaryOwner &+= 1
            summaryPoller = nil
            isTerminated = true
            terminator()
        } catch {
            isQuitting = false
            presentRecovery(for: .backendExited)
        }
    }

    private func reloadSummary() async {
        guard let summaryPoller else {
            summary = MenuSummaryModel()
            return
        }
        let owner = summaryOwner
        let requestOwner = beginSummaryRequest()
        let result = await summaryPoller.reload()
        await acceptSummary(
            result,
            from: summaryPoller,
            owner: owner,
            requestOwner: requestOwner
        )
    }

    func pollSummaryIfDue() async {
        guard let summaryPoller
        else { return }
        let owner = summaryOwner
        guard let ownedResult = await summaryPoller.poll(
            isActive: isActive,
            requestStarted: { self.beginSummaryRequest() }
        )
        else { return }
        await acceptSummary(
            ownedResult.result,
            from: summaryPoller,
            owner: owner,
            requestOwner: ownedResult.requestOwner
        )
    }

    private func acceptSummary(
        _ result: MenuSummaryFetchResult,
        from poller: MenuSummaryPoller,
        owner: UInt64,
        requestOwner: UInt64
    ) async {
        await commitAfterNewestSummaryCheck(
            isNewest: {
                await poller.ownsNewest(result)
            },
            isStillOwned: {
                owner == self.summaryOwner
                    && requestOwner == self.summaryRequestOwner
                    && self.summaryPoller === poller
            },
            commit: {
                self.summary = MenuSummaryModel(summary: result.summary)
            }
        )
    }

    private func beginSummaryRequest() -> UInt64 {
        summaryRequestOwner &+= 1
        return summaryRequestOwner
    }

    private func startPollingIfNeeded() {
        guard isActive,
              session != nil,
              pollingTask == nil,
              !isQuitting,
              !isTerminated
        else { return }
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(60))
                } catch {
                    return
                }
                guard let self else { return }
                await self.pollSummaryIfDue()
            }
        }
    }

    private func presentRecovery(for error: BackendError) {
        recoveryIssue = error == .backendNotFound
            ? .installationRequired
            : .backendUnavailable
    }
}

@MainActor
func commitAfterNewestSummaryCheck(
    isNewest: () async -> Bool,
    isStillOwned: () -> Bool,
    commit: () -> Void
) async {
    guard await isNewest(), isStillOwned() else { return }
    commit()
}

private struct UnknownSummaryFetcher: MenuSummaryFetching {
    func fetch() async throws -> MenuSummary {
        .unknown
    }
}

private final class BackendExitRelay: @unchecked Sendable {
    private let lock = NSLock()
    private var handler: (@Sendable (BackendError) -> Void)?

    @MainActor
    func attach(_ coordinator: AppCoordinator) {
        lock.withLock {
            handler = { [weak coordinator] error in
                Task { @MainActor in
                    coordinator?.backendExited(error)
                }
            }
        }
    }

    func report(_ error: BackendError) {
        let callback = lock.withLock { handler }
        callback?(error)
    }
}
