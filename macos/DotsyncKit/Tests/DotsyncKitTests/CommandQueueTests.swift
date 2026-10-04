import Testing
@testable import DotsyncKit

@MainActor
private final class Log {
    var entries: [String] = []
}

@MainActor
@Test func aCommandAskedForWhileAnotherRunsWaitsItsTurn() async {
    // "저장 안 하고 바꾸기" pressed during a refresh, and the switch after
    // "먼저 저장하기…" (always inside the refresh the login starts), were
    // dropped without a word.
    let queue = CommandQueue()
    let log = Log()
    let (released, release) = AsyncStream<Void>.makeStream()
    let refresh = Task {
        await queue.run("조회 중") {
            log.entries.append("refresh")
            for await _ in released {}
        }
    }
    while log.entries.isEmpty { await Task.yield() }
    let use = Task { await queue.run("교체 중") { log.entries.append("use") } }
    for _ in 0..<20 { await Task.yield() }
    #expect(queue.running == "조회 중")
    #expect(log.entries == ["refresh"])

    release.finish()
    await refresh.value
    await use.value

    #expect(log.entries == ["refresh", "use"])
    #expect(queue.running == nil)
}

@MainActor
@Test func waitingCommandsRunInTheOrderAskedFor() async {
    let queue = CommandQueue()
    let log = Log()
    let (released, release) = AsyncStream<Void>.makeStream()
    let first = Task {
        await queue.run("조회 중") {
            log.entries.append("first")
            for await _ in released {}
        }
    }
    while log.entries.isEmpty { await Task.yield() }
    var others: [Task<Void, Never>] = []
    for name in ["second", "third", "fourth"] {
        others.append(Task { await queue.run(name) { log.entries.append(name) } })
        for _ in 0..<5 { await Task.yield() }
    }

    release.finish()
    await first.value
    for other in others { await other.value }

    #expect(log.entries == ["first", "second", "third", "fourth"])
    #expect(queue.running == nil)
}
