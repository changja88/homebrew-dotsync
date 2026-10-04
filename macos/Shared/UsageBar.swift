import DotsyncKit
import SwiftUI

/// A usage bar: green below 60 %, yellow below 85 %, red from 85 %. In a
/// skeleton (redacted as placeholder) only the empty track shows.
struct UsageBar: View {
    let percent: Int
    var height: CGFloat = 6
    @Environment(\.redactionReasons) private var redactionReasons

    private var shown: Int { redactionReasons.contains(.placeholder) ? 0 : min(max(percent, 0), 100) }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule()
                    .fill(Self.color(percent))
                    .frame(width: geometry.size.width * CGFloat(shown) / 100)
            }
        }
        .frame(height: height)
    }

    static func color(_ percent: Int) -> Color {
        switch UsageText.tier(percent) {
        case .low: .green
        case .mid: .yellow
        case .high: .red
        }
    }
}
