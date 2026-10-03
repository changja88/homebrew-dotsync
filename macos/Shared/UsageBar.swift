import DotsyncKit
import SwiftUI

/// A usage bar: green below 60 %, yellow below 85 %, red from 85 %.
struct UsageBar: View {
    let percent: Int
    var height: CGFloat = 6

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule()
                    .fill(Self.color(percent))
                    .frame(width: geometry.size.width * CGFloat(min(max(percent, 0), 100)) / 100)
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
