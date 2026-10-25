import AppKit
import SwiftUI

// The window's buttons, badge and ground. The glass itself is in Shared/Glass.swift.

/// "사용", "계정 추가" (blue) and "새로고침", "다시 로그인" (white glass).
struct PillButtonStyle: ButtonStyle {
    var prominent = false
    var height: CGFloat = 28
    /// 1.5 lifts a white pill off a white sheet (the mockups' 0 1px 3px).
    var shadowRadius: CGFloat = 0
    /// The mockups' small buttons are 12 pt with 10-12 pt sides, their sheet buttons 13 pt with 16-20.
    var fontSize: CGFloat? = nil
    var sidePadding: CGFloat? = nil
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .labelStyle(.titleAndIcon)
            .font(.system(size: fontSize ?? (height > 30 ? 13 : 12.5), weight: .semibold))
            .foregroundStyle(prominent ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
            .padding(.horizontal, sidePadding ?? (prominent ? 16 : 12))
            .frame(height: height)
            .background {
                if prominent {
                    BlueFill()
                } else {
                    GlassPanel(cornerRadius: height / 2, milk: 0.7, shadowRadius: shadowRadius)
                }
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
            .background(GlassPanel(cornerRadius: size / 2, milk: 0.7, shadowRadius: 0))
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

/// A message on orange-tinted glass that the person dismisses.
struct MessageBar: View {
    let text: String
    let dismiss: () -> Void

    var body: some View {
        HStack {
            Text(text).font(.callout)
            Spacer()
            Button(action: dismiss) { Image(systemName: "xmark") }
                .buttonStyle(.borderless)
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(GlassPanel(cornerRadius: 12, tint: .orange, shadowRadius: 0))
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

/// The see-through ground with the mockup's pastel light over it. Without
/// the light the glass turns grey over a white window behind.
struct WindowBackdrop: View {
    var body: some View {
        ZStack {
            ActiveBackdrop()
            PastelLight()
        }
    }
}
