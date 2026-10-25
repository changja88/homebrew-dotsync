import SwiftUI

/// The one dotsync window: the rail, and the chosen menu beside it. The title
/// bar is hidden so the rail runs the window's full height (mockup `D-usage`).
struct RootView: View {
    let accounts: AccountsModel
    let settings: SettingsModel
    let sessions: SessionsModel
    /// Remembered across launches.
    @AppStorage("rail") private var selection: RailItem = .usage

    var body: some View {
        HStack(spacing: 0) {
            Rail(selection: $selection, badges: [.settings: settings.badge])
            Group {
                switch selection {
                case .usage: AccountsWindow(model: accounts)
                case .settings: SettingsView(model: settings)
                case .sessions: SessionsView(model: sessions)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
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
            Spacer(minLength: 0)
            trailing
        }
        .padding(EdgeInsets(top: 14, leading: 20, bottom: 4, trailing: 18))
        .frame(height: 52)
    }
}
