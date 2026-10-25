import SwiftUI

/// The window's menus, top to bottom.
enum RailItem: String, CaseIterable, Identifiable {
    case usage
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .usage: "사용량"
        case .settings: "설정"
        }
    }

    var systemImage: String {
        switch self {
        case .usage: "gauge.with.needle"
        case .settings: "slider.horizontal.3"
        }
    }
}

/// The 76 pt column down the window's left edge (mockup `D-usage`): the
/// window's buttons on top, then one item per menu.
struct Rail: View {
    @Binding var selection: RailItem
    var badges: [RailItem: Int] = [:]
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let palette = RailPalette(dark: colorScheme == .dark)
        VStack(spacing: 6) {
            ForEach(RailItem.allCases) { item in
                RailButton(item: item, isSelected: item == selection, badge: badges[item] ?? 0, palette: palette) {
                    selection = item
                }
            }
            Spacer(minLength: 0)
        }
        // Room for the window's buttons: 16 above them, 12 for them, 22 below.
        .padding(.top, 50).padding(.bottom, 14)
        .frame(width: 76)
        .frame(maxHeight: .infinity)
        .background(palette.side)
        .overlay(alignment: .trailing) { Rectangle().fill(palette.line).frame(width: 1) }
    }
}

/// One menu: its symbol over its name, tinted when chosen, with a count in
/// the top-right corner when the menu has something to look at.
struct RailButton: View {
    let item: RailItem
    let isSelected: Bool
    let badge: Int
    let palette: RailPalette
    let select: () -> Void
    @State private var isHovered = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 12, style: .continuous)
        Button(action: select) {
            VStack(spacing: 4) {
                Image(systemName: item.systemImage)
                    .font(.system(size: 17, weight: .medium))
                    .frame(width: 22, height: 22)
                Text(item.title).font(.system(size: 10.5, weight: .semibold))
            }
            .padding(.top, 9).padding(.bottom, 7)
            .frame(width: 60)
            .foregroundStyle(isSelected ? palette.accent : palette.secondary)
            .background(shape.fill(isSelected ? palette.selected : isHovered ? palette.line : .clear))
            .overlay(alignment: .topTrailing) {
                if badge > 0 { RailBadge(count: badge).padding(.top, 4).padding(.trailing, 8) }
            }
            .contentShape(shape)
        }
        .buttonStyle(.plain)
        .onHover { isHovered = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

/// The rail's count: white on the mockup's dark orange.
struct RailBadge: View {
    let count: Int

    var body: some View {
        Text("\(count)")
            .font(.system(size: 10, weight: .bold)).monospacedDigit()
            .foregroundStyle(.white)
            .padding(.horizontal, 4)
            .frame(minWidth: 16)
            .frame(height: 16)
            .background(Capsule().fill(Color(red: 173 / 255, green: 82 / 255, blue: 0)))
    }
}

/// The mockups' rail colors, light and dark.
struct RailPalette {
    let dark: Bool

    var side: Color { dark ? Color(red: 30 / 255, green: 32 / 255, blue: 40 / 255).opacity(0.32) : .white.opacity(0.26) }
    var line: Color {
        dark ? Color(red: 235 / 255, green: 235 / 255, blue: 245 / 255).opacity(0.09)
            : Color(red: 60 / 255, green: 60 / 255, blue: 67 / 255).opacity(0.1)
    }
    var selected: Color {
        dark ? Color(red: 10 / 255, green: 132 / 255, blue: 1).opacity(0.28)
            : Color(red: 10 / 255, green: 122 / 255, blue: 1).opacity(0.14)
    }
    var accent: Color {
        dark ? Color(red: 108 / 255, green: 182 / 255, blue: 1) : Color(red: 10 / 255, green: 104 / 255, blue: 220 / 255)
    }
    var secondary: Color {
        dark ? Color(red: 235 / 255, green: 235 / 255, blue: 245 / 255).opacity(0.66)
            : Color(red: 29 / 255, green: 29 / 255, blue: 31 / 255).opacity(0.64)
    }
}
