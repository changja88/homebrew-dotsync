import Foundation
import Testing
@testable import DotsyncKit

func temporaryDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("dotsynckit-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

@Test func storeSavesAndLoads() throws {
    let store = UsageStore(directory: try temporaryDirectory().appendingPathComponent("nested"))
    var file = UsageFile.empty
    file.active = "a"
    try store.save(file)
    #expect(store.load() == file)
    #expect(store.fileURL.lastPathComponent == "usage.json")
}

@Test func storeLoadsNothingBeforeTheFirstSave() throws {
    #expect(UsageStore(directory: try temporaryDirectory()).load() == nil)
}

@Test func storeIgnoresAnUnreadableFile() throws {
    let store = UsageStore(directory: try temporaryDirectory())
    try Data("{\"version\": 1, \"accou".utf8).write(to: store.fileURL)
    #expect(store.load() == nil)
}

@Test func appLinksRoundTrip() {
    for link in [AppLink.open, .use("changja00"), .relogin("a.b-c_d")] {
        #expect(AppLink(url: link.url) == link)
    }
    #expect(AppLink.use("bob").url.absoluteString == "dotsync://use?name=bob")
}

@Test func appLinksRejectOtherURLs() {
    #expect(AppLink(url: URL(string: "https://example.com/use?name=bob")!) == nil)
    #expect(AppLink(url: URL(string: "dotsync://use")!) == nil)
    #expect(AppLink(url: URL(string: "dotsync://nope")!) == nil)
}
