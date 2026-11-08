#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

@MainActor
protocol ObjectExploring {
    func roots() -> [String: JSONValue]
    func explore(_ payload: [String: JSONValue]) -> [String: JSONValue]
    func set(_ payload: [String: JSONValue]) -> [String: JSONValue]
    func call(_ payload: [String: JSONValue]) -> [String: JSONValue]
    func instances(_ payload: [String: JSONValue]) -> [String: JSONValue]
}

/// Remote object explorer: shortcut roots, live instances, property reads and writes,
/// and zero-argument method calls. Objects are reached only through registry handles.
@MainActor
final class ObjectExplorerService: ObjectExploring {
    static let shared = ObjectExplorerService()
    static let maxInstances = 200

    private let registry: ObjectHandleRegistering
    private var reader: ObjectValueReader { ObjectValueReader(registry: registry) }

    init(registry: ObjectHandleRegistering = ObjectHandleRegistry.shared) {
        self.registry = registry
    }

    func roots() -> [String: JSONValue] {
        let rows = rootObjects().map { name, object in
            JSONValue.object(["name": .string(name), "value": reader.encode(object)])
        }
        return ["roots": .array(rows)]
    }

    func explore(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        guard let object = resolve(payload) else { return Self.failure(.notFound) }
        return reader.explore(object)
    }

    func set(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        guard let object = resolve(payload) else { return Self.failure(.notFound) }
        let key = Self.text(payload["key"])
        switch ObjectValueWriter.set(object, key: key, value: payload["value"] ?? .null) {
        case .success:
            var out = reader.explore(object)
            out["updated"] = .string(key)
            return out
        case let .failure(error):
            return Self.failure(error)
        }
    }

    func call(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        guard let object = resolve(payload) else { return Self.failure(.notFound) }
        let selector = Self.text(payload["selector"])
        switch ObjectMethodInvoker.call(object, selector: selector, reader: reader) {
        case let .success(value):
            return ["selector": .string(selector), "handle": .string(registry.register(object)), "result": value]
        case let .failure(error):
            return Self.failure(error)
        }
    }

    func instances(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        let name = Self.text(payload["class"])
        guard !name.isEmpty, let cls = NSClassFromString(name) else { return Self.failure(.notFound) }
        var limit = Self.maxInstances
        if case let .number(value) = payload["limit"] {
            limit = max(1, min(Self.maxInstances, Int(safe: value, fallback: Self.maxInstances)))
        }
        let rows = HeapInstanceScan.instances(of: cls, limit: limit).map { reader.encode($0) }
        return [
            "class": .string(name),
            "instances": .array(rows),
            "truncated": .bool(rows.count >= limit)
        ]
    }

    /// Accepts a registry handle, a `ui.hierarchy` view id, or a memory tracker id.
    private func resolve(_ payload: [String: JSONValue]) -> AnyObject? {
        let handle = Self.text(payload["handle"])
        if !handle.isEmpty { return registry.object(for: handle) }
        #if canImport(UIKit)
        let viewId = Self.text(payload["view_id"])
        if !viewId.isEmpty { return UIInspectionService.findView(id: viewId) }
        #endif
        let memoryId = Self.text(payload["memory_id"])
        if !memoryId.isEmpty { return MemoryDebugService.shared.trackedObject(id: memoryId) }
        return nil
    }

    private func rootObjects() -> [(String, AnyObject)] {
        var out: [(String, AnyObject)] = [
            ("Bundle.main", Bundle.main),
            ("ProcessInfo", ProcessInfo.processInfo),
            ("UserDefaults.standard", UserDefaults.standard),
            ("URLCache.shared", URLCache.shared),
            ("HTTPCookieStorage.shared", HTTPCookieStorage.shared),
            ("URLSession.shared", URLSession.shared),
            ("FileManager.default", FileManager.default),
            ("NotificationCenter.default", NotificationCenter.default)
        ]
        #if canImport(UIKit)
        if let app = AutomationScreen.application {
            out.insert(("UIApplication.shared", app), at: 0)
            if let delegate = app.delegate { out.insert(("App delegate", delegate), at: 1) }
        }
        if let window = AutomationScreen.keyWindow() {
            out.append(("Key window", window))
            if let root = window.rootViewController {
                out.append(("Root view controller", root))
                let top = Self.topController(root)
                if top !== root { out.append(("Top view controller", top)) }
            }
        }
        out.append(("UIDevice.current", UIDevice.current))
        out.append(("UIScreen.main", UIScreen.main))
        #endif
        return out
    }

    #if canImport(UIKit)
    private static func topController(_ root: UIViewController) -> UIViewController {
        var current = root
        while true {
            if let presented = current.presentedViewController {
                current = presented
            } else if let navigation = current as? UINavigationController,
                      let visible = navigation.visibleViewController {
                guard visible !== current else { return current }
                current = visible
            } else if let tabs = current as? UITabBarController, let selected = tabs.selectedViewController {
                current = selected
            } else {
                return current
            }
        }
    }
    #endif

    private static func text(_ value: JSONValue?) -> String {
        if case let .string(item) = value { return item }
        return ""
    }

    static func failure(_ error: ObjectExplorerError) -> [String: JSONValue] {
        let message: String
        switch error {
        case .notFound: message = "Object not found or already released"
        case let .unsupportedType(type): message = "Unsupported type: \(type)"
        case let .readOnly(key): message = "Read-only: \(key)"
        case let .blockedSelector(name): message = "Selector not allowed: \(name)"
        case let .invalidValue(reason): message = reason
        case let .exception(reason): message = "Objective-C exception: \(reason)"
        }
        return ["error": .string(message)]
    }
}
#endif
