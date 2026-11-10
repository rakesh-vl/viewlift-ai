#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
#if os(iOS) || os(tvOS)
import UserNotifications
#endif

/// Local notifications for QA: content, image attachment, action buttons, and the responses the
/// tester taps. tvOS shows only the app badge, so everything else is ignored there.
enum PushDebugService {
    static let identifierPrefix = "viewlift-debug-"
    static let categoryIdentifier = "viewlift.debug.actions"
    static let maxActions = 4
    static let maxImageBytes = 10_000_000

    #if os(iOS) || os(tvOS)
    static func schedule(payload: [String: JSONValue]) async throws -> [String: JSONValue] {
        let input = AutomationPayload(payload)
        let center = UNUserNotificationCenter.current()
        let authorization = try await authorize(center)
        guard authorization != "denied" else {
            return [
                "scheduled": .bool(false),
                "authorization": .string(authorization),
                "detail": .string("Notifications are off for this app. Turn them on in Settings, then try again.")
            ]
        }
        let content = UNMutableNotificationContent()
        if let badge = input.number("badge") {
            content.badge = NSNumber(value: max(0, Int(safe: badge)))
        }
        #if os(tvOS)
        guard content.badge != nil else {
            return ["scheduled": .bool(false), "detail": .string("tvOS shows only a badge. Pass badge.")]
        }
        #endif
        var notes: [JSONValue] = []
        #if os(iOS)
        notes = await fill(content, input: input, payload: payload, center: center)
        #endif
        let delay = min(max(input.number("delay") ?? 1, 1), 86_400)
        let identifier = identifierPrefix + UUID().uuidString
        let request = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
        )
        try await center.add(request)
        PushDebugDelegate.installIfNeeded(center)
        return [
            "scheduled": .bool(true),
            "id": .string(identifier),
            "delay": .number(delay),
            "authorization": .string(authorization),
            "badge_only": .bool(isTV),
            "attachment": .bool(!content.attachmentsList.isEmpty),
            "responses_tracked": .bool(PushDebugDelegate.isInstalled(center)),
            "notes": .array(notes)
        ]
    }

    static func status() async -> [String: JSONValue] {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        let pending = await center.pendingNotificationRequests()
            .filter { $0.identifier.hasPrefix(identifierPrefix) }
        var out: [String: JSONValue] = [
            "authorization": .string(name(settings.authorizationStatus)),
            "badge": .bool(settings.badgeSetting == .enabled),
            "pending": .array(pending.map(row)),
            "responses_tracked": .bool(PushDebugDelegate.isInstalled(center)),
            "responses": .array(PushDebugDelegate.responses())
        ]
        #if os(iOS)
        out["alert"] = .bool(settings.alertSetting == .enabled)
        out["sound"] = .bool(settings.soundSetting == .enabled)
        let delivered = await center.deliveredNotifications()
            .filter { $0.request.identifier.hasPrefix(identifierPrefix) }
        out["delivered"] = .number(Double(delivered.count))
        #endif
        return out
    }

    static func clear() async -> [String: JSONValue] {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
            .map(\.identifier)
            .filter { $0.hasPrefix(identifierPrefix) }
        center.removePendingNotificationRequests(withIdentifiers: pending)
        var removed = pending.count
        #if os(iOS)
        let delivered = await center.deliveredNotifications()
            .map(\.request.identifier)
            .filter { $0.hasPrefix(identifierPrefix) }
        center.removeDeliveredNotifications(withIdentifiers: delivered)
        removed += delivered.count
        #endif
        PushDebugDelegate.clearResponses()
        return ["removed": .number(Double(removed))]
    }

    private static func authorize(_ center: UNUserNotificationCenter) async throws -> String {
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .notDetermined else {
            return name(settings.authorizationStatus)
        }
        let granted = try await center.requestAuthorization(options: [.alert, .sound, .badge])
        return granted ? "authorized" : "denied"
    }

    private static func row(_ request: UNNotificationRequest) -> JSONValue {
        var fields: [String: JSONValue] = ["id": .string(request.identifier)]
        #if os(iOS)
        fields["title"] = .string(request.content.title)
        #endif
        if let trigger = request.trigger as? UNTimeIntervalNotificationTrigger,
           let date = trigger.nextTriggerDate() {
            fields["fires_at"] = .number(date.timeIntervalSince1970)
        }
        return .object(fields)
    }

    static func name(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .provisional: return "provisional"
        case .notDetermined: return "not_determined"
        #if os(iOS)
        case .ephemeral: return "ephemeral"
        #endif
        @unknown default: return "unknown"
        }
    }

    private static var isTV: Bool {
        #if os(tvOS)
        return true
        #else
        return false
        #endif
    }
    #else
    static func schedule(payload: [String: JSONValue]) async throws -> [String: JSONValue] {
        throw DebugBridgeError.unsupportedCommand
    }

    static func status() async -> [String: JSONValue] {
        ["authorization": .string("unsupported")]
    }

    static func clear() async -> [String: JSONValue] {
        ["removed": .number(0)]
    }
    #endif
}

#if os(iOS) || os(tvOS)
private extension UNMutableNotificationContent {
    var attachmentsList: [Any] {
        #if os(iOS)
        return attachments
        #else
        return []
        #endif
    }
}
#endif
#endif
