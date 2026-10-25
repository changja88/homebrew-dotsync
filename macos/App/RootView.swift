import SwiftUI

/// The one dotsync window: the rail, and the chosen menu beside it. The title
/// bar is hidden so the rail runs the window's full height (mockup `D-usage`).
struct RootView: View {
    let accounts: AccountsModel
    let settings: SettingsModel
    let sessions: SessionsModel
    /// Quitting that waits here for long work to end.
    let quitting: AppDelegate
    /// Remembered across launches.
    @AppStorage("rail") private var selection: RailItem = .usage
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        HStack(spacing: 0) {
            Rail(selection: $selection, badges: [.settings: settings.badge])
            VStack(spacing: 0) {
                // "정리가 끝나면 꺼져요" and the progress; ✕ keeps dotsync open.
                if let notice = quitting.quitNotice {
                    MessageBar(text: notice) { quitting.keepOpen() }
                        .padding(EdgeInsets(top: 14, leading: 20, bottom: 0, trailing: 18))
                }
                Group {
                    switch selection {
                    case .usage: AccountsWindow(model: accounts)
                    case .settings: SettingsView(model: settings, codexSessionWork: sessions[.codex].work != nil)
                    case .sessions: SessionsView(model: sessions)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .ignoresSafeArea(.container, edges: .top)
        .frame(minWidth: 1080, minHeight: 640)
        // Looks the same focused or not: macOS would dim what follows the
        // window's state in a window that isn't in front.
        .environment(\.appearsActive, true)
        // The desktop shows through, behind the glass panels.
        .containerBackground(for: .window) { WindowBackdrop() }
        // Every dotsync:// link comes from a usage widget.
        .onOpenURL { url in
            selection = .usage
            accounts.handle(url)
        }
        // Once per window, not on every return to 사용량.
        .task { await accounts.refresh() }
        .onAppear { quitting.showWindow = { openWindow(id: "main") } }
    }
}

/// A menu's title row, as in the mockups: 52 pt high, the title in 15 pt
/// bold, the menu's controls on the right.
struct ContentHeader<Trailing: View>: View {
    let title: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 12) {
            Text(title).font(.system(size: 15, weight: .bold))
            trailing.frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(EdgeInsets(top: 14, leading: 20, bottom: 4, trailing: 18))
        .frame(height: 52)
    }
}
