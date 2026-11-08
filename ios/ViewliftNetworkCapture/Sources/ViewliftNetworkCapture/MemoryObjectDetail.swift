#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

/// Everything the dashboard inspector shows for one tracked object:
/// identity, lifetime, where it was created, and what keeps it alive.
enum MemoryObjectDetail {
    static let superclassLimit = 8

    @MainActor
    static func make(
        object: AnyObject,
        record: MemoryTrackedRecord,
        leaked: Bool,
        others: [(id: String, object: AnyObject)]
    ) -> [String: JSONValue] {
        let executable = Bundle.main.executableURL?.lastPathComponent ?? ""
        let frames = CrashHangRecorder.parseFrames(record.symbols)
        let objectClass: AnyClass = type(of: object)
        var detail: [String: JSONValue] = [
            "found": .bool(true),
            "id": .string(record.id),
            "class": .string(record.className),
            "module": .string(moduleName(objectClass)),
            "superclasses": .array(superclasses(of: objectClass).map(JSONValue.string)),
            "context": .string(record.context),
            "screen": .string(record.screen),
            "created_at": .number(record.createdAt.timeIntervalSince1970),
            "age_seconds": .number(Date().timeIntervalSince(record.createdAt)),
            "leaked": .bool(leaked),
            "instance_bytes": .number(Double(class_getInstanceSize(objectClass))),
            "executable": .string(executable),
            "creation_frames": .array(frames.map(JSONValue.object)),
            "retention": .object(RetentionInspector.inspect(root: object, others: others))
        ]
        if let focus = HangFrameFocus.suspect(frames: frames, executable: executable) {
            detail["suspect"] = .object(focus)
        }
        #if canImport(UIKit)
        if let controller = object as? UIViewController {
            detail["view_controller"] = .object(viewControllerState(controller))
        }
        #endif
        return detail
    }

    /// Small row for `memory.snapshot` and the live sample object list.
    static func row(_ item: MemoryTrackedRecord, leaked: Bool) -> JSONValue {
        .object([
            "id": .string(item.id),
            "class": .string(item.className),
            "context": .string(item.context),
            "screen": .string(item.screen),
            "leaked": .bool(leaked),
            "created_at": .number(item.createdAt.timeIntervalSince1970),
            "age_seconds": .number(Date().timeIntervalSince(item.createdAt))
        ])
    }

    static func missing(id: String) -> [String: JSONValue] {
        [
            "found": .bool(false),
            "id": .string(id),
            "error": .string("This object was released or is no longer tracked.")
        ]
    }

    private static func moduleName(_ objectClass: AnyClass) -> String {
        let full = NSStringFromClass(objectClass)
        guard let dot = full.firstIndex(of: ".") else { return "Objective-C" }
        return String(full[..<dot])
    }

    private static func superclasses(of objectClass: AnyClass) -> [String] {
        var names: [String] = []
        var current: AnyClass? = class_getSuperclass(objectClass)
        while let item = current, names.count < superclassLimit {
            names.append(NSStringFromClass(item))
            current = class_getSuperclass(item)
        }
        return names
    }

    #if canImport(UIKit)
    @MainActor
    private static func viewControllerState(_ controller: UIViewController) -> [String: JSONValue] {
        let parent = controller.parent.map { String(describing: type(of: $0)) } ?? ""
        let presenter = controller.presentingViewController.map { String(describing: type(of: $0)) } ?? ""
        return [
            "title": .string(controller.title ?? controller.navigationItem.title ?? ""),
            "view_loaded": .bool(controller.isViewLoaded),
            "in_window": .bool(controller.viewIfLoaded?.window != nil),
            "parent": .string(parent),
            "presented_by": .string(presenter),
            "children": .number(Double(controller.children.count))
        ]
    }
    #endif
}
#endif
