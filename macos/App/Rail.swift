import SwiftUI

/// The window's menus, top to bottom.
enum RailItem: String, CaseIterable, Identifiable {
    case usage
    case settings
    case sessions
    case autoSwitch

    var id: String { rawValue }

    var title: String {
        switch self {
        case .usage: "사용량"
        case .settings: "설정"
        case .sessions: "세션"
        case .autoSwitch: "자동 교체"
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
        // Room for the window's buttons: 16 above them, 12 for them, 22 below,
        // and the mockup's 6 pt gap before the first item.
        .padding(.top, 56).padding(.bottom, 14)
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
                RailIcon(item: item)
                    .stroke(style: StrokeStyle(lineWidth: 1.9 * 22 / 24, lineCap: .round, lineJoin: .round))
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

/// The mockup's rail icons, drawn from its 24 × 24 SVG paths: no SF Symbol
/// has its open gauge or its two sliders.
struct RailIcon: Shape {
    let item: RailItem

    func path(in rect: CGRect) -> Path {
        let scale = min(rect.width, rect.height) / 24
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + x * scale, y: rect.minY + y * scale)
        }
        func circle(_ x: CGFloat, _ y: CGFloat, _ radius: CGFloat) -> CGRect {
            CGRect(x: rect.minX + (x - radius) * scale, y: rect.minY + (y - radius) * scale,
                   width: radius * 2 * scale, height: radius * 2 * scale)
        }
        var path = Path()
        switch item {
        case .usage:
            // "M4.2 17a8.5 8.5 0 1 1 15.6 0": the long way over the top.
            path.addArc(center: point(12, 17 - (8.5 * 8.5 - 7.8 * 7.8).squareRoot()), radius: 8.5 * scale,
                        startAngle: .degrees(156.6), endAngle: .degrees(23.4), clockwise: false)
            path.move(to: point(12, 14.5))
            path.addLine(to: point(15.8, 9.6))
            path.addEllipse(in: circle(12, 14.5, 1.3))
        case .settings:
            for (from, to) in [((4, 7), (13, 7)), ((19, 7), (20, 7)), ((4, 17), (7, 17)), ((13, 17), (20, 17))] {
                path.move(to: point(CGFloat(from.0), CGFloat(from.1)))
                path.addLine(to: point(CGFloat(to.0), CGFloat(to.1)))
            }
            path.addEllipse(in: circle(16, 7, 2.4))
            path.addEllipse(in: circle(10, 17, 2.4))
        case .sessions:
            // "M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5h-7l-4.5 3.5v-3.5H5A1.5 1.5 0 0 1 3.5 15V6A1.5 1.5 0 0 1 5 4.5z"
            path.move(to: point(5, 4.5))
            path.addLine(to: point(19, 4.5))
            path.addArc(tangent1End: point(20.5, 4.5), tangent2End: point(20.5, 6), radius: 1.5 * scale)
            path.addLine(to: point(20.5, 15))
            path.addArc(tangent1End: point(20.5, 16.5), tangent2End: point(19, 16.5), radius: 1.5 * scale)
            path.addLine(to: point(12, 16.5))
            path.addLine(to: point(7.5, 20))
            path.addLine(to: point(7.5, 16.5))
            path.addLine(to: point(5, 16.5))
            path.addArc(tangent1End: point(3.5, 16.5), tangent2End: point(3.5, 15), radius: 1.5 * scale)
            path.addLine(to: point(3.5, 6))
            path.addArc(tangent1End: point(3.5, 4.5), tangent2End: point(5, 4.5), radius: 1.5 * scale)
            path.closeSubpath()
            // "M8 9h8M8 12h5"
            path.move(to: point(8, 9))
            path.addLine(to: point(16, 9))
            path.move(to: point(8, 12))
            path.addLine(to: point(13, 12))
        case .autoSwitch:
            // Two arrows taking turns: "m17 2 4 4-4 4", "M3 11v-1a4 4 0 0 1 4-4h14",
            // "m7 22-4-4 4-4", "M21 13v1a4 4 0 0 1-4 4H3".
            path.move(to: point(17, 2))
            path.addLine(to: point(21, 6))
            path.addLine(to: point(17, 10))
            path.move(to: point(3, 11))
            path.addLine(to: point(3, 10))
            path.addArc(tangent1End: point(3, 6), tangent2End: point(7, 6), radius: 4 * scale)
            path.addLine(to: point(21, 6))
            path.move(to: point(7, 22))
            path.addLine(to: point(3, 18))
            path.addLine(to: point(7, 14))
            path.move(to: point(21, 13))
            path.addLine(to: point(21, 14))
            path.addArc(tangent1End: point(21, 18), tangent2End: point(17, 18), radius: 4 * scale)
            path.addLine(to: point(3, 18))
        }
        return path
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
