#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

public final class MemoryDebugService: @unchecked Sendable {
    public static let shared = MemoryDebugService()

    private struct WeakObject: @unchecked Sendable {
        weak var value: AnyObject?
        let id: String
        let className: String
        let createdAt: Date
        let context: String
    }

    private let lock = NSLock()
    private var tracked: [String: WeakObject] = [:]
    private var suspectedLeaks: [String: WeakObject] = [:]
    private var installed = false

    private init() {}

    public func installLifecycleTracking() {
        #if canImport(UIKit)
        lock.lock()
        guard !installed else {
            lock.unlock()
            return
        }
        installed = true
        lock.unlock()
        UIViewController.viewliftInstallLeakTracking()
        #endif
    }

    public func track(_ object: AnyObject, context: String = "registered") {
        let id = String(ObjectIdentifier(object).hashValue)
        let item = WeakObject(
            value: object,
            id: id,
            className: NSStringFromClass(type(of: object)),
            createdAt: Date(),
            context: context
        )
        lock.lock()
        tracked[id] = item
        lock.unlock()
    }

    func checkAfterDelay(_ object: AnyObject, context: String) {
        track(object, context: context)
        let id = String(ObjectIdentifier(object).hashValue)
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self, weak object] in
            guard let self, object != nil else { return }
            self.lock.lock()
            if let item = self.tracked[id] {
                self.suspectedLeaks[id] = item
            }
            self.lock.unlock()
        }
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        tracked = tracked.filter { $0.value.value != nil }
        suspectedLeaks = suspectedLeaks.filter { $0.value.value != nil }
        let trackedRows = tracked.values.map(Self.describe)
        let leakRows = suspectedLeaks.values.map(Self.describe)
        lock.unlock()
        return [
            "tracked": .array(trackedRows),
            "suspected_leaks": .array(leakRows),
            "tracked_count": .number(Double(trackedRows.count)),
            "leak_count": .number(Double(leakRows.count))
        ]
    }

    func graph() -> [String: JSONValue] {
        lock.lock()
        let live = tracked.values.filter { $0.value != nil }
        lock.unlock()
        let identifiers = Dictionary(
            uniqueKeysWithValues: live.compactMap { item in
                item.value.map { (ObjectIdentifier($0), item.id) }
            }
        )
        let nodes = live.map { item -> JSONValue in
            .object([
                "id": .string(item.id),
                "class": .string(item.className),
                "context": .string(item.context),
                "age_seconds": .number(Date().timeIntervalSince(item.createdAt))
            ])
        }
        var edges: [JSONValue] = []
        for item in live {
            guard let object = item.value else { continue }
            var currentClass: AnyClass? = type(of: object)
            while let cls = currentClass {
                var count: UInt32 = 0
                let ivars = class_copyIvarList(cls, &count)
                defer { free(ivars) }
                if let ivars {
                    for index in 0..<Int(count) {
                        let ivar = ivars[index]
                        guard let encoding = ivar_getTypeEncoding(ivar),
                              String(cString: encoding).hasPrefix("@"),
                              let target = object_getIvar(object, ivar),
                              let targetId = identifiers[ObjectIdentifier(target as AnyObject)] else {
                            continue
                        }
                        let name = ivar_getName(ivar).map(String.init(cString:)) ?? "ivar"
                        edges.append(.object([
                            "from": .string(item.id),
                            "to": .string(targetId),
                            "label": .string(name)
                        ]))
                    }
                }
                currentClass = class_getSuperclass(cls)
            }
        }
        return [
            "nodes": .array(nodes),
            "edges": .array(edges),
            "coverage": .string(
                "Registered Objective-C objects and lifecycle suspects; arbitrary pure Swift edges unavailable"
            )
        ]
    }

    private static func describe(_ item: WeakObject) -> JSONValue {
        .object([
            "id": .string(item.id),
            "class": .string(item.className),
            "context": .string(item.context),
            "age_seconds": .number(Date().timeIntervalSince(item.createdAt))
        ])
    }
}

#if canImport(UIKit)
private extension UIViewController {
    static func viewliftInstallLeakTracking() {
        guard
            let original = class_getInstanceMethod(
                UIViewController.self,
                #selector(UIViewController.viewDidDisappear(_:))
            ),
            let replacement = class_getInstanceMethod(
                UIViewController.self,
                #selector(UIViewController.viewliftViewDidDisappear(_:))
            )
        else {
            return
        }
        method_exchangeImplementations(original, replacement)
    }

    @objc func viewliftViewDidDisappear(_ animated: Bool) {
        viewliftViewDidDisappear(animated)
        guard isBeingDismissed || navigationController?.isBeingDismissed == true
            || isMovingFromParent else {
            return
        }
        MemoryDebugService.shared.checkAfterDelay(
            self,
            context: "dismissed-view-controller"
        )
    }
}
#endif
#endif
