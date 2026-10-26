import DotsyncKit
import UserNotifications

/// Auto switch's macOS notifications. Without permission they are dropped:
/// the switch and the log don't depend on them (spec 5.6).
enum AutoSwitchNotices {
    static func post(_ notice: AutoSwitchNotice) async {
        let content = UNMutableNotificationContent()
        content.title = notice.title
        content.body = notice.body
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }
}
