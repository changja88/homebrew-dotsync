import DotsyncKit
import Foundation
import Observation

/// What a delete (or recover) run leaves on screen. Tasks 19 and 21 fill these.
enum SessionSheet {
    case confirm
    case result(DeleteReport)
    case cleanup
}

/// A delete, recover or cleanup run in flight, for quitting and the 설정 rows.
struct SessionWork: Equatable {
    var label: String
    var done = 0
    var total = 0
}

/// One tool's list on the 세션 menu, and what the person did to it.
struct ToolSessions {
    var list: SessionList?
    /// From asking for a list to its answer, the wait in the queue included.
    var checking = false
    var message: String?
    var selection: Set<String> = []
    /// Row ids (group keys, or "merged") that are open, and the ones showing
    /// every session instead of the first five.
    var expanded: Set<String> = []
    var showAll: Set<String> = []
    var sheet: SessionSheet?
    var work: SessionWork?
}

/// The 세션 menu's state. Each tool has its own queue, so two commands for the
/// same tool never overlap while Claude Code and Codex may run together.
@MainActor
@Observable
final class SessionsModel {
    private(set) var tools: [SessionTool: ToolSessions] = [.claude: ToolSessions(), .codex: ToolSessions()]
    let queues: [SessionTool: CommandQueue]
    let service: SessionService?

    init(claudeQueue: CommandQueue, codexQueue: CommandQueue, service: SessionService? = SessionService.standard()) {
        queues = [.claude: claudeQueue, .codex: codexQueue]
        self.service = service
        if service == nil {
            let message = "dotsync를 찾을 수 없어요 — brew install changja88/dotsync/dotsync"
            for tool in SessionTool.allCases { tools[tool]?.message = message }
        }
    }

    subscript(tool: SessionTool) -> ToolSessions {
        tools[tool] ?? ToolSessions()
    }

    /// A delete or cleanup is running, for quitting to wait for.
    var isWorking: Bool { tools.values.contains { $0.work != nil } }

    func dismissMessage(_ tool: SessionTool) {
        tools[tool]?.message = nil
    }

    /// A list takes seconds to minutes, so it runs when a tab is first shown
    /// and on 다시 계산, never on a timer.
    func checkFirstTime(_ tool: SessionTool) async {
        guard self[tool].list == nil, !self[tool].checking else { return }
        await refresh(tool)
    }

    /// A failed list keeps the one on screen and says why; the selection is
    /// pruned only against a list that arrived.
    func refresh(_ tool: SessionTool) async {
        guard let service, let queue = queues[tool], !self[tool].checking else { return }
        tools[tool]?.checking = true
        await queue.run("확인 중…") {
            do {
                accept(try await service.list(tool), for: tool)
            } catch {
                tools[tool]?.message = SettingsModel.describe(error)
            }
        }
        tools[tool]?.checking = false
    }

    private func accept(_ list: SessionList, for tool: SessionTool) {
        guard var state = tools[tool] else { return }
        if state.list == nil, let first = SessionRules.initiallyExpanded(SessionRules.rows(list.groups)) {
            state.expanded = [first]
        }
        state.list = list
        state.selection = SessionRules.pruned(state.selection, in: list)
        state.message = nil
        tools[tool] = state
    }

    /// Letting go of a pick always works; picking needs an idle session of the
    /// current list.
    func toggle(_ id: String, in tool: SessionTool) {
        if tools[tool]?.selection.remove(id) != nil { return }
        guard let list = tools[tool]?.list, let item = SessionRules.item(id, in: list),
              SessionRules.isSelectable(item) else { return }
        tools[tool]?.selection.insert(id)
    }

    /// `key` is a group's key, or "merged" for the row of the small ones.
    func toggleGroup(_ key: String, in tool: SessionTool) {
        guard let state = tools[tool], let list = state.list,
              let row = SessionRules.rows(list.groups).first(where: { $0.id == key }) else { return }
        tools[tool]?.selection = SessionRules.togglingGroup(state.selection, selectable: SessionRules.selectable(in: row))
    }

    func toggleExpanded(_ key: String, in tool: SessionTool) {
        if tools[tool]?.expanded.remove(key) == nil { tools[tool]?.expanded.insert(key) }
    }

    func showAll(_ key: String, in tool: SessionTool) {
        tools[tool]?.showAll.insert(key)
    }

    /// Replaces the selection with the idle sessions with no record for `days`.
    func quickSelect(days: Int, tool: SessionTool) {
        guard let list = tools[tool]?.list else { return }
        tools[tool]?.selection = SessionRules.quickSelect(list, olderThanDays: days, now: .now)
    }

    func clearSelection(_ tool: SessionTool) {
        tools[tool]?.selection = []
    }
}
