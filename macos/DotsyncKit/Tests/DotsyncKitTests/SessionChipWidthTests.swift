import AppKit
import Testing
@testable import DotsyncKit

/// The chip is `Text` in the system font, 11 pt bold, between 8 pt of padding
/// (`SessionChip` in SessionsView.swift); the running chip also has a 6 pt
/// dot and 5 pt of spacing.
@Test func theStatusColumnShowsEveryChipWholeAndNoWider() {
    let font = NSFont.systemFont(ofSize: 11, weight: .bold)
    func chipWidth(_ text: String, dot: Bool = false) -> CGFloat {
        let text = (text as NSString).size(withAttributes: [.font: font]).width
        return 8 + (dot ? 6 + 5 : 0) + text + 8
    }
    var widths = ["running", "unknown", "background", "desktop", "interrupted"].map { state -> CGFloat in
        chipWidth(SessionText.chip(makeItem("a", state: state))!, dot: state == "running")
    }
    // A group row's "실행 중 N" chip, up to three digits.
    widths += [chipWidth("실행 중 999", dot: true)]
    let widest = widths.max()!
    #expect(SessionText.statusColumnWidth >= widest)
    #expect(SessionText.statusColumnWidth < widest + 1, "the column is wider than the widest chip needs")
}
