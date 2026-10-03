import Foundation
import Testing
@testable import DotsyncKit

/// The shape `dotsync account usage --json` prints (Part 1, spec Part 1).
let sampleReport = """
{"fetched_at": "2026-10-04T12:00:00Z", "active": "onelife", "unsaved_seat": null,
 "accounts": [
  {"name": "onelife", "label": "onelife", "email": "onelife.godeeper@gmail.com", "status": "ok",
   "five_hour": {"percent": 31, "resets_at": "2026-10-04T15:39:00Z"},
   "seven_day": {"percent": 15, "resets_at": "2026-10-09T05:59:00Z"}},
  {"name": "team", "label": "팀", "email": null, "status": "login_required", "five_hour": null, "seven_day": null},
  {"name": "side", "label": "side", "email": "side@example.com", "status": "error", "error": "timeout",
   "five_hour": {"percent": 0, "resets_at": null}, "seven_day": null}
 ]}
"""

@Test func decodesTheUsageReport() throws {
    let report = try UsageJSON.decoder().decode(UsageReport.self, from: Data(sampleReport.utf8))
    #expect(report.active == "onelife")
    #expect(report.unsavedSeat == nil)
    #expect(report.fetchedAt == Date(timeIntervalSince1970: 1_791_115_200))
    #expect(report.accounts.map(\.status) == [.ok, .loginRequired, .error])
    #expect(report.accounts[0].fiveHour == UsageWindow(percent: 31, resetsAt: Date(timeIntervalSince1970: 1_791_128_340)))
    #expect(report.accounts[1].label == "팀")
    #expect(report.accounts[2].error == "timeout")
    #expect(report.accounts[2].fiveHour?.resetsAt == nil)
}

@Test func decodesAnUnsavedSeat() throws {
    let json = """
    {"fetched_at": "2026-10-04T12:00:00Z", "active": null, "accounts": [],
     "unsaved_seat": {"email": "u@example.com", "status": "ok",
       "five_hour": {"percent": 5, "resets_at": "2026-10-04T13:00:00Z"}, "seven_day": null}}
    """
    let report = try UsageJSON.decoder().decode(UsageReport.self, from: Data(json.utf8))
    #expect(report.unsavedSeat?.email == "u@example.com")
    #expect(report.unsavedSeat?.fiveHour?.percent == 5)
}

@Test func usageFileRoundTripsWithSnakeCaseKeys() throws {
    var file = UsageFile.empty
    file.fetchedAt = Date(timeIntervalSince1970: 1_791_115_200)
    file.active = "a"
    file.accounts = [AccountUsage(name: "a", label: "A", email: nil, status: .ok,
                                  fiveHour: UsageWindow(percent: 1, resetsAt: nil),
                                  fetchedAt: Date(timeIntervalSince1970: 1_791_115_200))]
    let data = try UsageJSON.encoder().encode(file)
    let text = String(decoding: data, as: UTF8.self)
    #expect(text.contains("\"fetched_at\" : \"2026-10-04T12:00:00Z\""))
    #expect(text.contains("\"five_hour\""))
    #expect(try UsageJSON.decoder().decode(UsageFile.self, from: data) == file)
}

@Test func activeAccountIsTheSavedAccountInUse() {
    var file = UsageFile.empty
    file.accounts = [AccountUsage(name: "a", label: "a", email: nil, status: .ok),
                     AccountUsage(name: "b", label: "b", email: nil, status: .ok)]
    file.active = "b"
    #expect(file.activeAccount?.name == "b")
    file.active = nil
    #expect(file.activeAccount == nil)
}

@Test func decodesAccountInfoAndErrors() throws {
    let info = try UsageJSON.decoder().decode(
        AccountInfo.self,
        from: Data(#"{"name": "bob", "label": "밥", "email": "b@x", "logged_in": true}"#.utf8))
    #expect(info == AccountInfo(name: "bob", label: "밥", email: "b@x", loggedIn: true))
    let envelope = try UsageJSON.decoder().decode(
        CLIErrorEnvelope.self,
        from: Data(#"{"error": {"code": "unsaved_login", "message": "m", "email": "u@x"}}"#.utf8))
    #expect(envelope.error == CLIError(code: "unsaved_login", message: "m", email: "u@x"))
}
