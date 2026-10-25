import Foundation

/// The Korean copy of the session screen: chips, sub lines, totals and the
/// lines after a delete. The wording is fixed by the F boards and the spec.
public enum SessionText {
    public static func bytes(_ count: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }

    /// "244개 · 33.4 GB · 실행 중 11"; "실행 중" is left out when nothing runs.
    public static func totals(_ list: SessionList) -> String {
        var parts = ["\(grouped(list.totals.sessions))개", bytes(list.totals.bytes)]
        if list.totals.running > 0 { parts.append("실행 중 \(list.totals.running)") }
        return parts.joined(separator: " · ")
    }

    private static func grouped(_ count: Int) -> String {
        count.formatted(.number.locale(Locale(identifier: "en_US")))
    }

    /// "방금" under a minute, then "N분 전", "N시간 전", "N일 전".
    public static func relative(_ date: Date, now: Date) -> String {
        let minutes = Int(now.timeIntervalSince(date) / 60)
        if minutes < 1 { return "방금" }
        if minutes < 60 { return "\(minutes)분 전" }
        if minutes < 1440 { return "\(minutes / 60)시간 전" }
        return "\(minutes / 1440)일 전"
    }

    /// The badge of a session that cannot be picked; nil for an idle one.
    public static func chip(_ item: SessionItem) -> String? {
        switch item.state {
        case "running": return "실행 중"
        case "unknown": return "확인 못 함"
        case "background": return "agent view에 있음"
        case "desktop": return "Claude 앱 세션"
        case "interrupted": return "지우다 멈춤"
        default: return nil
        }
    }

    /// The folder, then what else is worth knowing about the session.
    public static func subLine(_ item: SessionItem, tool: SessionTool) -> String {
        var parts = [item.folder]
        if let n = item.subagents, n > 0 { parts.append("하위 에이전트 기록 \(n)개") }
        if let n = item.children, n > 0 { parts.append("하위 세션 \(n)개도 같이 지워져요") }
        if item.reason == "recent" { parts.append("30분 안에 기록됐어요") }
        if item.archived == true { parts.append("보관됨") }
        if let reason = item.reason, reason.hasPrefix("agents_") || reason == "process_scan_failed" {
            parts.append("실행 목록을 읽지 못했어요")
        }
        if item.reason == "unexplained_process" { parts.append("같은 폴더에서 실행 중인 Claude가 있어요") }
        if tool == .codex, item.reason == "scan_failed" { parts.append("실행 여부를 확인하지 못했어요") }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// What a group row says under its name; `selected` counts the picked
    /// sessions of the group.
    public static func groupSubLine(_ group: SessionGroup, selected: Int) -> String {
        var line: String
        switch group.key {
        case "outside": line = "임시 폴더처럼 저장소가 아닌 곳에서 연 세션"
        case "unknown": line = unknownSubLine(group)
        default:
            line = "작업 폴더 \(group.folders)개"
            if group.foldersDeleted > 0 { line += " · \(group.foldersDeleted)개는 지워져서 경로로 찾았어요" }
        }
        if selected > 0 { line += " · \(selected)개 선택" }
        return line
    }

    /// Why the places are unknown, when every session has the same reason.
    private static func unknownSubLine(_ group: SessionGroup) -> String {
        let reasons = Set(group.items.map { $0.placeReason ?? "" })
        switch reasons.count == 1 ? reasons.first : nil {
        case "not_found": return "폴더가 지워져서 어느 저장소인지 몰라요"
        case "cwd_unreadable": return "폴더를 읽을 수 없어요"
        case "git_failed": return "저장소인지 확인하지 못했어요"
        default: return "어느 저장소인지 확인하지 못했어요"
        }
    }

    /// The hint under the list.
    public static func bottomRule(tool: SessionTool, historyDbBytes: Int64?) -> String {
        switch tool {
        case .claude:
            return "실행 중이거나 방금 기록된 세션은 고를 수 없어요 · 크기는 세션 파일과 그 세션만 쓰는 폴더를 합친 값이에요"
        case .codex:
            let store = historyDbBytes.map { "공유 대화 저장소(\(bytes($0)))" } ?? "공유 대화 저장소"
            return "실행 중이거나 확인 못 한 세션은 고를 수 없어요 · 크기에 \(store)는 빠져요"
        }
    }

    // MARK: Results

    /// "2개를 지웠고 1개는 건너뛰었어요": one clause per kind of outcome.
    public static func resultTitle(_ report: DeleteReport) -> String {
        let results = report.results
        guard !results.isEmpty else { return "지운 게 없어요" }
        let counts = [
            (results.filter { $0.outcome == "deleted" }.count, "지웠"),
            (results.filter { $0.outcome == "restored" }.count, "되돌렸"),
            (results.filter { $0.outcome == "partial" }.count, "일부만 지웠"),
            (results.filter { !["deleted", "restored", "partial"].contains($0.outcome) }.count, "건너뛰었"),
        ].filter { $0.0 > 0 }
        let clauses = counts.enumerated().map { index, entry in
            index == 0 ? "\(entry.0)개를 \(entry.1)" : "\(entry.0)개는 \(entry.1)"
        }
        return clauses.joined(separator: "고 ") + "어요"
    }

    /// Why a session was skipped, by the reason the CLI gives. A reason with a
    /// `unknown:` prefix is one the liveness check could not settle.
    static let skippedLines: [String: String] = [
        "in_use": "Codex가 거절했어요. 그사이 다른 창에서 열렸어요",
        "referenced": "이 세션에서 갈라져 나온 세션이 아직 이 기록을 써서 지울 수 없어요",
        "stopped": "멈춰서 지우지 않았어요",
        "running": "그사이 다시 열려서 건너뛰었어요",
        "not_found": "이미 없어요",
        "refused": "Codex가 지우지 않았어요. 대화 기록은 그대로예요",
        "invalid_id": "id 형식이 달라서 건드리지 않았어요",
        "codex_missing": "Codex 프로그램을 찾지 못해서 지우지 않았어요",
        "scan_failed": "사용 중인지 확인하지 못해서 건너뛰었어요",
        "unsafe_path": "경로가 안전하지 않아서 건드리지 않았어요",
        "inspect_failed": "남은 기록을 확인하지 못해서 건너뛰었어요",
        "not_root": "하위 세션이라 따로 지우지 않았어요",
        "rename_failed": "파일을 옮기지 못해서 건너뛰었어요",
        "recent": "방금 기록돼서 건너뛰었어요",
        "background": "agent view에 있어서 건너뛰었어요",
        "desktop": "Claude 앱 세션이라 건너뛰었어요",
    ]
    /// `unknown:<reason>` is a session the liveness check could not clear;
    /// the sub-reason says why. Anything not listed is "could not tell".
    static let unknownLines: [String: String] = [
        "new_transcript": "그사이 새로 기록돼서 건너뛰었어요",
        "recent": skippedLines["recent"]!,
        "unsafe_path": skippedLines["unsafe_path"]!,
        "unexplained_process": "같은 폴더에서 실행 중인 Claude가 있어서 건너뛰었어요",
    ]
    static let unknownFallback = "실행 여부를 확인하지 못해서 건너뛰었어요"
    static let restoreFailedLine = "되돌리지 못했어요. 지우다 멈춘 상태로 남아 있어요"
    static let skippedFallback = "건너뛰었어요"

    /// The partial reasons the CLI emits; they share one line, and
    /// `no_rollout` adds a pointer to the cleanup.
    static let partialReasons: Set<String> = ["failed", "timeout", "no_rollout"]
    static let partialFallback = "일부만 지웠을 수 있어요"

    /// One sentence group for one result, without its size.
    public static func resultLine(_ result: DeleteResult, tool: SessionTool) -> String {
        var sentences: [String]
        switch result.outcome {
        case "deleted":
            sentences = [deletedLine(result, tool: tool)]
            if result.kept?.contains("tool_results") == true {
                sentences.append("다른 세션이 쓸 수 있는 도구 결과는 남겼어요")
            }
        case "partial":
            sentences = [partialFallback]
            if tool == .codex, result.reason == "no_rollout" { sentences.append("나머지는 \"남은 데이터 정리\"에서 지워져요") }
            if result.unchecked?.isEmpty == false { sentences.append("남은 게 있는지 확인하지 못했어요") }
        case "restored":
            sentences = ["되돌렸어요. 다시 이어서 열 수 있어요"]
        case "failed":
            sentences = [restoreFailedLine]
        default:
            sentences = [skippedLine(reason: result.reason)]
        }
        return sentences.joined(separator: ". ")
    }

    private static func deletedLine(_ result: DeleteResult, tool: SessionTool) -> String {
        if tool == .codex, let children = result.children, children > 0 {
            return "하위 세션 \(children)개까지 모두 지웠어요"
        }
        return "지웠어요"
    }

    private static func skippedLine(reason: String?) -> String {
        guard let reason else { return skippedFallback }
        if let line = skippedLines[reason] { return line }
        guard reason.hasPrefix("unknown:") else { return skippedFallback }
        return unknownLines[String(reason.dropFirst("unknown:".count))] ?? unknownFallback
    }
}
