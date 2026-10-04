import DotsyncKit
import Foundation

extension AccountUsage {
    /// The week is used up: its row says only when it resets, tinted red, in
    /// the window and in the widget.
    func isWeekUsedUp(now: Date) -> Bool {
        if case .fullWeek = WidgetLayout.kind(of: self, now: now) { return true }
        return false
    }
}
