#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime
#if os(iOS)
import UserNotifications
#endif

#if canImport(UIKit)
import UIKit
#endif

final class MacroRecorder: @unchecked Sendable {
    static let shared = MacroRecorder()

    private let lock = NSLock()
    private var recording = false
    private var startedAt = Date()
    private var actions: [[String: JSONValue]] = []
    private var installed = false

    private init() {}

    func install() {
        #if canImport(UIKit)
        lock.lock()
        guard !installed else {
            lock.unlock()
            return
        }
        installed = true
        lock.unlock()
        UIApplication.viewliftInstallActionRecording()
        #endif
    }

    func start() {
        lock.lock()
        actions = []
        startedAt = Date()
        recording = true
        lock.unlock()
    }

    func stop() -> [String: JSONValue] {
        lock.lock()
        recording = false
        let copy = actions
        lock.unlock()
        return ["actions": .array(copy.map(JSONValue.object))]
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let copy = actions
        let active = recording
        lock.unlock()
        return [
            "recording": .bool(active),
            "actions": .array(copy.map(JSONValue.object))
        ]
    }

    @MainActor
    func replay(_ payload: [String: JSONValue]) async -> [String: JSONValue] {
        #if canImport(UIKit)
        guard case let .array(items) = payload["actions"] else {
            return ["replayed": .number(0)]
        }
        var replayed = 0
        var previousOffset = 0.0
        for item in items.prefix(1_000) {
            guard case let .object(action) = item,
                  case let .string(selectorName) = action["selector"] else {
                continue
            }
            let offset: Double
            if case let .number(value) = action["offset"] {
                offset = value
            } else {
                offset = previousOffset
            }
            let wait = max(0, min(offset - previousOffset, 5))
            if wait > 0 {
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            }
            previousOffset = offset
            let identifier: String
            if case let .string(value) = action["identifier"] {
                identifier = value
            } else {
                identifier = ""
            }
            guard let control = findControl(identifier: identifier) else { continue }
            _ = UIApplication.shared.sendAction(
                NSSelectorFromString(selectorName),
                to: nil,
                from: control,
                for: nil
            )
            replayed += 1
        }
        return ["replayed": .number(Double(replayed))]
        #else
        return ["replayed": .number(0)]
        #endif
    }

    func record(selector: String, sender: Any?) {
        lock.lock()
        defer { lock.unlock() }
        guard recording else { return }
        #if canImport(UIKit)
        let view = sender as? UIView
        let identifier = view?.accessibilityIdentifier ?? ""
        let label = view?.accessibilityLabel ?? ""
        #else
        let identifier = ""
        let label = ""
        #endif
        actions.append([
            "selector": .string(selector),
            "identifier": .string(identifier),
            "label": .string(label),
            "offset": .number(Date().timeIntervalSince(startedAt))
        ])
        if actions.count > 1_000 {
            actions.removeFirst(actions.count - 1_000)
        }
    }

    #if canImport(UIKit)
    @MainActor
    private func findControl(identifier: String) -> UIControl? {
        guard !identifier.isEmpty else { return nil }
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        for window in windows {
            if let match = findControl(identifier: identifier, root: window) {
                return match
            }
        }
        return nil
    }

    private func findControl(identifier: String, root: UIView) -> UIControl? {
        if root.accessibilityIdentifier == identifier, let control = root as? UIControl {
            return control
        }
        for child in root.subviews {
            if let match = findControl(identifier: identifier, root: child) {
                return match
            }
        }
        return nil
    }
    #endif
}

enum PushDebugService {
    static func schedule(payload: [String: JSONValue]) async throws {
        #if os(iOS)
        let content = UNMutableNotificationContent()
        content.title = string(payload["title"])
        content.body = string(payload["body"])
        content.sound = payload["sound"] == .bool(false) ? nil : .default
        if case let .number(badge) = payload["badge"] {
            content.badge = NSNumber(value: Int(badge))
        }
        if case let .object(userInfo) = payload["user_info"] {
            content.userInfo = userInfo.mapValues(foundation)
        }
        let delay: TimeInterval
        if case let .number(value) = payload["delay"] {
            delay = max(1, min(value, 86_400))
        } else {
            delay = 1
        }
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: trigger
        )
        try await UNUserNotificationCenter.current().add(request)
        #else
        throw DebugBridgeError.unsupportedCommand
        #endif
    }

    #if os(iOS)
    private static func string(_ value: JSONValue?) -> String {
        if case let .string(text) = value {
            return text
        }
        return ""
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
    #endif
}

#if canImport(UIKit)
private extension UIApplication {
    static func viewliftInstallActionRecording() {
        guard
            let original = class_getInstanceMethod(
                UIApplication.self,
                #selector(UIApplication.sendAction(_:to:from:for:))
            ),
            let replacement = class_getInstanceMethod(
                UIApplication.self,
                #selector(UIApplication.viewliftSendAction(_:to:from:for:))
            )
        else {
            return
        }
        method_exchangeImplementations(original, replacement)
    }

    @objc func viewliftSendAction(
        _ action: Selector,
        to target: Any?,
        from sender: Any?,
        for event: UIEvent?
    ) -> Bool {
        MacroRecorder.shared.record(selector: NSStringFromSelector(action), sender: sender)
        return viewliftSendAction(action, to: target, from: sender, for: event)
    }
}
#endif
#endif
