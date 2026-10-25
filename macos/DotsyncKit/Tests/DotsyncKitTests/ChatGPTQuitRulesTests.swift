import Foundation
import Testing
@testable import DotsyncKit

@Test func aQuitIsDeclinedOnlyAfterSixtySeconds() {
    let sent = Date(timeIntervalSince1970: 1000)
    #expect(!ChatGPTQuitRules.declined(askedAt: sent, now: sent))
    #expect(!ChatGPTQuitRules.declined(askedAt: sent, now: sent.addingTimeInterval(60)))
    #expect(ChatGPTQuitRules.declined(askedAt: sent, now: sent.addingTimeInterval(60.5)))
}
