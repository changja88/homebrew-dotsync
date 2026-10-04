import SwiftUI

/// A usage ring, colored like `UsageBar`. In a skeleton (redacted as
/// placeholder) only the empty track shows.
struct UsageRing: View {
    let percent: Int
    var lineWidth: CGFloat = 5
    @Environment(\.redactionReasons) private var redactionReasons

    private var shown: Int { redactionReasons.contains(.placeholder) ? 0 : min(max(percent, 0), 100) }

    var body: some View {
        ZStack {
            Circle().stroke(.primary.opacity(0.1), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: CGFloat(shown) / 100)
                .stroke(UsageBar.color(percent), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
        }
        .padding(lineWidth / 2)
    }
}
