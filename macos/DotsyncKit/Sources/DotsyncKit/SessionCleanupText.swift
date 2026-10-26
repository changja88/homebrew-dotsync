import Foundation

/// The Korean copy of the Codex cleanup sheet (`F-codex-cleanup`): why a
/// cleanup was refused, what each item and the vacuum did, and what stays.
extension SessionText {
    /// A refused dry-run or a failed cleanup, by the CLI's code; any other
    /// code keeps the CLI's own message.
    public static func cleanupRefusal(_ error: CLIError) -> String {
        switch error.code {
        case "codex_running": return "Codex가 실행 중이라 정리하지 않았어요"
        case "codex_version": return "지금 Codex 기록이 dotsync가 아는 형태가 아니라서 정리할 수 없어요"
        case "incomplete_scan": return "읽지 못한 대화 기록이 있어서 정리하지 않았어요"
        case "codex_db_failed": return "Codex 저장소를 읽지 못해서 정리하지 않았어요"
        case "busy": return "다른 Codex 작업이 끝나면 다시 해 주세요"
        default: return error.message
        }
    }

    /// One item of the report: "5개 정리했어요", with the lines of
    /// `history_lines`, or why it did not run. `history_rows` goes one
    /// session at a time, so one that stopped or failed partway says what it
    /// had already removed (its count).
    public static func cleanupResultLine(_ result: CleanupItemResult) -> String {
        switch result.outcome {
        case "done":
            let lines = result.lines.map { " · \($0)줄" } ?? ""
            return "\(result.count)개 정리했어요\(lines)"
        case "skipped":
            switch result.reason {
            case "codex_started": return "그사이 Codex가 켜져서 멈췄어요"
            case "stopped": return result.count > 0 ? "\(result.count)개만 정리하고 멈췄어요" : "멈춰서 하지 않았어요"
            default: return "하지 않았어요"
            }
        case "failed":
            let line = result.count > 0 ? "\(result.count)개만 정리했어요" : "하지 못했어요"
            return result.message.map { "\(line) · \($0)" } ?? line
        default:
            return "하지 않았어요"
        }
    }

    /// What the vacuum did, then why it stopped. A vacuum that freed nothing
    /// has no "줄었어요" line.
    public static func vacuumLines(_ vacuum: VacuumResult) -> [String] {
        var lines: [String] = []
        if vacuum.bytesAfter < vacuum.bytesBefore {
            lines.append("대화 저장소가 \(bytes(vacuum.bytesBefore))에서 \(bytes(vacuum.bytesAfter))로 줄었어요")
        }
        switch vacuum.stoppedBecause {
        case "time_cap": lines.append("10분이 지나서 멈췄어요. 다음 정리 때 이어서 줄여요")
        case "busy": lines.append("저장소가 바빠서 멈췄어요")
        case "blocker": lines.append("Codex가 켜져서 멈췄어요")
        case "stopped": lines.append("멈췄어요")
        case "no_progress": lines.append("더 줄일 수 없었어요")
        case "error": lines.append("빈 공간을 돌려받지 못했어요")
        default: break  // freelist_empty: it freed all there was
        }
        return lines
    }

    /// The file under the "대화 저장소에 남은 내용" row (the CLI's `HISTORY_DB`).
    public static let historyDbName = "thread_history_1.sqlite"

    /// 그대로 두는 것.
    public static let cleanupKept: [(name: String, reason: String)] = [
        ("데스크톱 앱 상태", "ChatGPT 앱이 스스로 관리해요"),
        ("첨부 파일", "어느 세션 것인지 알 수 없어요"),
        ("생성 이미지", "다른 세션이 쓸 수 있어요"),
        ("로그", "Codex가 스스로 정리해요"),
    ]

    /// The names of the dry-run's items, for the report (the CLI's own labels).
    static let cleanupItemLabels: [String: String] = [
        "history_rows": "대화 저장소에 남은 내용",
        "history_lines": "입력 기록에서 지운 세션의 줄",
        "session_index": "이름 목록에서 지운 세션",
        "queue": "큐 기록",
    ]
}
