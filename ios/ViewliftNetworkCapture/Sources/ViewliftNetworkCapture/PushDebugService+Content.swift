#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
#if os(iOS) || os(tvOS)
import UserNotifications
#endif

#if os(iOS)
/// Text, image, and action buttons for an iOS test notification.
extension PushDebugService {
    static func fill(
        _ content: UNMutableNotificationContent,
        input: AutomationPayload,
        payload: [String: JSONValue],
        center: UNUserNotificationCenter
    ) async -> [JSONValue] {
        var notes: [JSONValue] = []
        content.title = input.text("title")
        content.subtitle = input.text("subtitle")
        content.body = input.text("body")
        content.sound = payload["sound"] == .bool(false) ? nil : .default
        content.threadIdentifier = input.string("thread_id")
        if case let .object(userInfo) = payload["user_info"] {
            content.userInfo = userInfo.mapValues(foundation)
        }
        do {
            if let attachment = try await attachment(input) {
                content.attachments = [attachment]
            }
        } catch {
            notes.append(.string("Image not attached: \(error.localizedDescription)"))
        }
        let actions = notificationActions(payload["actions"])
        if !actions.isEmpty {
            await register(actions, center: center)
            content.categoryIdentifier = categoryIdentifier
        }
        return notes
    }

    private static func attachment(_ input: AutomationPayload) async throws -> UNNotificationAttachment? {
        let encoded = input.string("image_base64")
        let link = input.string("image_url")
        let data: Data
        let type: String
        if !encoded.isEmpty {
            guard let decoded = Data(base64Encoded: encoded) else { throw PushImageError.unreadable }
            data = decoded
            type = input.string("image_type")
        } else if !link.isEmpty {
            (data, type) = try await download(link)
        } else {
            return nil
        }
        guard data.count <= maxImageBytes else { throw PushImageError.tooLarge }
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent(identifierPrefix + UUID().uuidString)
            .appendingPathExtension(fileExtension(type))
        try data.write(to: file, options: .atomic)
        return try UNNotificationAttachment(identifier: "image", url: file)
    }

    private static func download(_ link: String) async throws -> (Data, String) {
        guard let url = URL(string: link), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
            throw PushImageError.badURL
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw PushImageError.status(http.statusCode)
        }
        let type = response.mimeType ?? url.pathExtension
        return (data, type)
    }

    private static func fileExtension(_ type: String) -> String {
        let lowered = type.lowercased()
        if lowered.contains("png") { return "png" }
        if lowered.contains("gif") { return "gif" }
        return "jpg"
    }

    private static func notificationActions(_ value: JSONValue?) -> [UNNotificationAction] {
        guard case let .array(items) = value else { return [] }
        var out: [UNNotificationAction] = []
        for (index, item) in items.prefix(maxActions).enumerated() {
            guard case let .object(row) = item else { continue }
            let fields = AutomationPayload(row)
            let title = fields.string("title")
            guard !title.isEmpty else { continue }
            let identifier = fields.string("id").isEmpty ? "action-\(index + 1)" : fields.string("id")
            var options: UNNotificationActionOptions = []
            if fields.bool("destructive") { options.insert(.destructive) }
            if fields.bool("foreground") { options.insert(.foreground) }
            if fields.bool("input") {
                out.append(UNTextInputNotificationAction(
                    identifier: identifier, title: title, options: options,
                    textInputButtonTitle: "Send", textInputPlaceholder: title
                ))
            } else {
                out.append(UNNotificationAction(identifier: identifier, title: title, options: options))
            }
        }
        return out
    }

    /// Replaces only this SDK's category so the app's own categories keep working.
    private static func register(_ actions: [UNNotificationAction], center: UNUserNotificationCenter) async {
        let existing = await center.notificationCategories()
            .filter { $0.identifier != categoryIdentifier }
        let category = UNNotificationCategory(
            identifier: categoryIdentifier, actions: actions, intentIdentifiers: [], options: []
        )
        center.setNotificationCategories(existing.union([category]))
    }

    private static func foundation(_ value: JSONValue) -> Any {
        switch value {
        case let .string(item): return item
        case let .number(item): return item
        case let .bool(item): return item
        case let .array(item): return item.map(foundation)
        case let .object(item): return item.mapValues(foundation)
        case .null: return NSNull()
        }
    }
}

enum PushImageError: LocalizedError {
    case unreadable
    case tooLarge
    case badURL
    case status(Int)

    var errorDescription: String? {
        switch self {
        case .unreadable: return "the image data is not valid base64."
        case .tooLarge: return "the image is over 10 MB."
        case .badURL: return "the image URL must start with http:// or https://."
        case let .status(code): return "the image URL returned HTTP \(code)."
        }
    }
}
#endif

#if os(iOS) || os(tvOS)
/// Shows test banners while the app is open and records which action the tester tapped.
/// Installed only when the app has no notification delegate of its own.
final class PushDebugDelegate: NSObject, UNUserNotificationCenterDelegate, @unchecked Sendable {
    static let shared = PushDebugDelegate()
    private static let maxResponses = 30

    private let lock = NSLock()
    private var log: [JSONValue] = []

    static func installIfNeeded(_ center: UNUserNotificationCenter) {
        if center.delegate == nil {
            center.delegate = shared
        }
    }

    static func isInstalled(_ center: UNUserNotificationCenter) -> Bool {
        center.delegate === shared
    }

    static func responses() -> [JSONValue] {
        shared.lock.lock()
        defer { shared.lock.unlock() }
        return shared.log
    }

    static func clearResponses() {
        shared.lock.lock()
        shared.log = []
        shared.lock.unlock()
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        #if os(iOS)
        completionHandler([.banner, .list, .sound, .badge])
        #else
        completionHandler([.badge])
        #endif
    }

    #if os(iOS)
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        var row: [String: JSONValue] = [
            "time": .number(Date().timeIntervalSince1970),
            "id": .string(response.notification.request.identifier),
            "title": .string(response.notification.request.content.title),
            "action": .string(Self.actionName(response.actionIdentifier))
        ]
        if let text = response as? UNTextInputNotificationResponse {
            row["text"] = .string(String(text.userText.prefix(200)))
        }
        lock.lock()
        log.insert(.object(row), at: 0)
        if log.count > Self.maxResponses {
            log.removeLast(log.count - Self.maxResponses)
        }
        lock.unlock()
        completionHandler()
    }

    private static func actionName(_ identifier: String) -> String {
        switch identifier {
        case UNNotificationDefaultActionIdentifier: return "opened"
        case UNNotificationDismissActionIdentifier: return "dismissed"
        default: return identifier
        }
    }
    #endif
}
#endif
#endif
