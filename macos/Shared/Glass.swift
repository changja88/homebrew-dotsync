import SwiftUI

// Glass drawn by hand with the approved mockups' values, shared by the window
// and the widget. System Liquid Glass greys out whenever the window isn't in
// front, and a widget can't refract at all.

/// A glass panel: a milky fill that is brighter at the top, a sheen from the
/// top left, a rim lit at the top-left and bottom-right corners, and a shadow.
struct GlassPanel: View {
    let cornerRadius: CGFloat
    var tint: Color? = nil
    var tintStrength: Double = 0.08
    /// Milkier for controls (0.7) than for panels.
    var milk: Double = 0.62
    /// 0 for none.
    var shadowRadius: CGFloat = 14
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        let dark = colorScheme == .dark
        let base = dark ? Color(red: 0.24, green: 0.25, blue: 0.31) : Color.white
        let rim = Color.white.opacity(dark ? 0.45 : 1)
        let rimLow = Color.white.opacity(dark ? 0.05 : 0.25)
        let shade = Color(red: 0.09, green: 0.13, blue: 0.25)
        shape
            .fill(LinearGradient(colors: [base.opacity(min(milk + 0.15, 1)), base.opacity(max(milk - 0.1, 0))],
                                 startPoint: .top, endPoint: .bottom))
            .overlay { if let tint { shape.fill(tint.opacity(dark ? tintStrength * 1.7 : tintStrength)) } }
            .overlay(shape.fill(LinearGradient(stops: [.init(color: .white.opacity(dark ? 0.08 : 0.45), location: 0),
                                                       .init(color: .clear, location: 0.45)],
                                               startPoint: .topLeading, endPoint: .bottomTrailing)))
            .overlay(shape.strokeBorder(
                LinearGradient(stops: [.init(color: rim, location: 0), .init(color: rimLow, location: 0.34),
                                       .init(color: rimLow, location: 0.66), .init(color: rim, location: 1)],
                               startPoint: .topLeading, endPoint: .bottomTrailing),
                lineWidth: 1))
            .shadow(color: shade.opacity(shadowRadius > 0 ? (dark ? 0.38 : 0.12) : 0),
                    radius: shadowRadius, y: shadowRadius * 0.7)
            .shadow(color: shade.opacity(shadowRadius > 0 ? (dark ? 0.3 : 0.07) : 0), radius: 1, y: 1)
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
            .shadow(color: Color(red: 0.04, green: 0.45, blue: 0.95).opacity(0.3), radius: 4, y: 2)
    }
}

/// The mockups' pastel light: blue top left, pink top right, peach bottom
/// right, lilac bottom left, washed with white.
struct PastelLight: View {
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
            ForEach(blobs.indices, id: \.self) { index in
                EllipticalGradient(colors: [blobs[index].0.opacity(dark ? 0.45 : 0.5), .clear],
                                   center: blobs[index].1, startRadiusFraction: 0, endRadiusFraction: 0.62)
            }
            (dark ? Color.black.opacity(0.2) : Color.white.opacity(0.45))
        }
    }
}
