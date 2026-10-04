import AppKit
import SwiftUI

// The window's glass, drawn by hand with the approved mockup's values ("앱 A").
// System Liquid Glass greys out — tint, prominent buttons — whenever the window
// isn't in front; this keeps one look either way.

/// A glass panel: a milky fill, a bright rim lit from the top left and the
/// bottom right, and a soft drop shadow.
struct GlassPanel: View {
    let cornerRadius: CGFloat
    var tint: Color? = nil
    /// Milkier for controls (0.68) than for panels (0.42).
    var milk: Double = 0.42
    var shadow = true
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        let dark = colorScheme == .dark
        let rim = Color.white.opacity(dark ? 0.45 : 0.95)
        let rimLow = Color.white.opacity(dark ? 0.05 : 0.18)
        shape
            .fill(dark ? Color(red: 0.24, green: 0.24, blue: 0.30).opacity(milk * 0.9) : Color.white.opacity(milk))
            .overlay { if let tint { shape.fill(tint.opacity(dark ? 0.24 : 0.14)) } }
            .overlay(shape.strokeBorder(
                LinearGradient(stops: [.init(color: rim, location: 0), .init(color: rimLow, location: 0.34),
                                       .init(color: rimLow, location: 0.66), .init(color: rim, location: 1)],
                               startPoint: .topLeading, endPoint: .bottomTrailing),
                lineWidth: 1))
            .shadow(color: Color(red: 0.09, green: 0.13, blue: 0.25).opacity(shadow ? (dark ? 0.38 : 0.14) : 0),
                    radius: 14, y: 10)
            .shadow(color: Color(red: 0.09, green: 0.13, blue: 0.25).opacity(shadow ? (dark ? 0.3 : 0.08) : 0),
                    radius: 1, y: 1)
    }
}

/// The blue of the main buttons and the "사용 중" badge.
struct BlueFill: View {
    var body: some View {
        Capsule()
            .fill(LinearGradient(colors: [Color(red: 0.29, green: 0.65, blue: 1.0),
                                          Color(red: 0.04, green: 0.45, blue: 0.95)],
                                 startPoint: .top, endPoint: .bottom))
            .overlay(Capsule().strokeBorder(
                LinearGradient(colors: [.white.opacity(0.5), .clear], startPoint: .top, endPoint: .center),
                lineWidth: 1))
            .shadow(color: Color(red: 0.04, green: 0.45, blue: 0.95).opacity(0.32), radius: 5, y: 3)
    }
}

/// "사용", "계정 추가" (blue) and "새로고침", "다시 로그인" (white glass).
struct PillButtonStyle: ButtonStyle {
    var prominent = false
    var height: CGFloat = 28
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .labelStyle(.titleAndIcon)
            .font(.system(size: height > 30 ? 13 : 12.5, weight: .semibold))
            .foregroundStyle(prominent ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
            .padding(.horizontal, prominent ? 16 : 12)
            .frame(height: height)
            .background {
                if prominent { BlueFill() } else { GlassPanel(cornerRadius: height / 2, milk: 0.68, shadow: false) }
            }
            .contentShape(Capsule())
            .opacity(configuration.isPressed ? 0.7 : isEnabled ? 1 : 0.5)
    }
}

/// The round white glass "⋯".
struct CircleGlassIcon: View {
    let systemName: String
    var size: CGFloat = 28

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(.primary)
            .frame(width: size, height: size)
            .background(GlassPanel(cornerRadius: size / 2, milk: 0.68, shadow: false))
            .contentShape(Circle())
    }
}

/// "사용 중": white on the blue fill.
struct BlueBadge: View {
    let text: String
    var color: Color? = nil

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background {
                if let color { Capsule().fill(color) } else { BlueFill() }
            }
    }
}

/// The window's see-through ground. Apple's "peeking through the back of the
/// window" material, kept active when the window is not in front (a SwiftUI
/// material would follow the window's state).
struct ActiveBackdrop: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .underWindowBackground
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
}

/// The see-through ground with the mockup's pastel light over it: blue top
/// left, pink top right, peach bottom right, lilac bottom left. Without it the
/// glass turns grey over a white window behind.
struct WindowBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let dark = colorScheme == .dark
        let blobs: [(Color, UnitPoint)] = dark
            ? [(Color(red: 0.11, green: 0.30, blue: 0.62), UnitPoint(x: 0.12, y: 0.18)),
               (Color(red: 0.40, green: 0.16, blue: 0.42), UnitPoint(x: 0.88, y: 0.22)),
               (Color(red: 0.48, green: 0.26, blue: 0.09), UnitPoint(x: 0.72, y: 0.92)),
               (Color(red: 0.23, green: 0.16, blue: 0.53), UnitPoint(x: 0.18, y: 0.88))]
            : [(Color(red: 0.58, green: 0.77, blue: 1.0), UnitPoint(x: 0.12, y: 0.18)),
               (Color(red: 0.96, green: 0.71, blue: 0.83), UnitPoint(x: 0.88, y: 0.22)),
               (Color(red: 1.0, green: 0.83, blue: 0.61), UnitPoint(x: 0.72, y: 0.92)),
               (Color(red: 0.73, green: 0.66, blue: 1.0), UnitPoint(x: 0.18, y: 0.88))]
        ZStack {
            ActiveBackdrop()
            (dark ? Color.black.opacity(0.25) : Color.white.opacity(0.3))
            ForEach(blobs.indices, id: \.self) { index in
                EllipticalGradient(colors: [blobs[index].0.opacity(dark ? 0.45 : 0.4), .clear],
                                   center: blobs[index].1, startRadiusFraction: 0, endRadiusFraction: 0.62)
            }
        }
    }
}
