import AppKit
import SwiftUI
import DotSyncNative

@main
struct DotSyncMenuApp: App {
    @StateObject private var coordinator = AppCoordinator.production()

    var body: some Scene {
        MenuBarExtra {
            PopoverRoot(coordinator: coordinator)
                .frame(width: 360, height: 560)
        } label: {
            Label(
                coordinator.summary.menuTitle,
                systemImage: "arrow.triangle.2.circlepath.circle.fill"
            )
        }
        .menuBarExtraStyle(.window)

        Window("DotSync", id: "manager") {
            ManagerRoot(coordinator: coordinator)
                .frame(minWidth: 920, minHeight: 620)
        }
        .defaultSize(width: 1180, height: 760)
    }
}

struct PopoverRoot: View {
    @ObservedObject var coordinator: AppCoordinator
    @Environment(\.openWindow) private var openWindow
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        root
            .onAppear {
                coordinator.installWindowOpener {
                    openWindow(id: "manager")
                }
                coordinator.surfaceAppeared(.popover)
            }
            .onChange(of: scenePhase) { phase in
                coordinator.setActive(phase == .active)
            }
    }

    @ViewBuilder
    private var root: some View {
        if coordinator.recoveryIssue != nil {
            RecoveryPanel(coordinator: coordinator)
        } else if let session = coordinator.session {
            WebSurface(
                origin: session.origin,
                processPool: coordinator.processPool,
                surface: .popover,
                destination: .overview
            ) { command in
                Task { await coordinator.handle(command) }
            }
        } else {
            ProgressView("Starting DotSync…")
        }
    }
}

struct ManagerRoot: View {
    @ObservedObject var coordinator: AppCoordinator
    @Environment(\.openWindow) private var openWindow
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        root
            .onAppear {
                coordinator.installWindowOpener {
                    openWindow(id: "manager")
                }
                coordinator.surfaceAppeared(.manager)
            }
            .onDisappear {
                coordinator.managerDidClose()
            }
            .onChange(of: scenePhase) { phase in
                coordinator.setActive(phase == .active)
            }
    }

    @ViewBuilder
    private var root: some View {
        if coordinator.recoveryIssue != nil {
            RecoveryPanel(coordinator: coordinator)
        } else if let session = coordinator.session {
            WebSurface(
                origin: session.origin,
                processPool: coordinator.processPool,
                surface: .manager,
                destination: coordinator.managerDestination,
                handoffs: coordinator.managerHandoffs,
                handoffAcknowledged: { sequence in
                    coordinator.acknowledgeManagerHandoff(sequence: sequence)
                }
            ) { command in
                Task { await coordinator.handle(command) }
            }
        } else {
            ProgressView("Starting DotSync…")
        }
    }
}

private struct RecoveryPanel: View {
    @ObservedObject var coordinator: AppCoordinator

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 32))
                .foregroundStyle(.orange)
            Text(coordinator.recoveryTitle)
                .font(.headline)
                .multilineTextAlignment(.center)
            HStack {
                Button("Retry") {
                    Task { await coordinator.retry() }
                }
                Button("Open installation help") {
                    coordinator.openInstallationHelp()
                }
                Button("Quit") {
                    Task { await coordinator.quit() }
                }
            }
        }
        .padding(24)
    }
}
