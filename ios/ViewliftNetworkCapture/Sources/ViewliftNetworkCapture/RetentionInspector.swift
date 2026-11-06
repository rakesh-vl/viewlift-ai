#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime
#if canImport(UIKit)
import UIKit
#endif

/// One outgoing object reference found through Swift reflection or Objective-C ivars.
struct ObjectReference {
    let label: String
    let target: AnyObject
}

/// Walks stored properties to explain why an object is still alive.
/// Swift reflection cannot tell weak from strong, so cycles are reported as candidates.
enum RetentionInspector {
    static let maxDepth = 4
    static let maxNodes = 60
    static let maxElements = 16

    static func inspect(root: AnyObject, others: [(id: String, object: AnyObject)]) -> [String: JSONValue] {
        var walk = RetentionWalk(root: root)
        walk.run()
        var result: [String: JSONValue] = [
            "nodes": .array(walk.nodes),
            "edges": .array(walk.edges),
            "cycles": .array(walk.cycles.map { .array($0.map(JSONValue.string)) }),
            "retained_by": .array(retainers(of: root, others: others)),
            "hints": .array((walk.hints + ownershipHints(root)).map(JSONValue.string))
        ]
        if walk.truncated {
            result["truncated"] = .bool(true)
        }
        return result
    }

    static func references(of object: AnyObject) -> [ObjectReference] {
        var out: [ObjectReference] = []
        var mirror: Mirror? = Mirror(reflecting: object)
        while let current = mirror {
            for child in current.children {
                collect(label: child.label ?? "_", value: child.value, into: &out)
            }
            mirror = current.superclassMirror
        }
        if out.isEmpty {
            out = ivarReferences(of: object)
        }
        return out
    }

    static func closureLabels(of object: AnyObject) -> [String] {
        var labels: [String] = []
        var mirror: Mirror? = Mirror(reflecting: object)
        while let current = mirror {
            for child in current.children where isClosure(child.value) {
                labels.append(child.label ?? "_")
            }
            mirror = current.superclassMirror
        }
        return labels
    }

    static func isSystemClass(_ name: String) -> Bool {
        let prefixes = ["UI", "NS", "_", "CA", "AV", "OS_", "__", "WK", "CF", "Swift."]
        return prefixes.contains { name.hasPrefix($0) }
    }

    static func typeName(_ object: AnyObject) -> String {
        String(describing: type(of: object))
    }

    static func objectId(_ object: AnyObject) -> String {
        String(UInt(bitPattern: ObjectIdentifier(object).hashValue), radix: 16)
    }

    private static func collect(label: String, value: Any, into out: inout [ObjectReference]) {
        let mirror = Mirror(reflecting: value)
        switch mirror.displayStyle {
        case .optional:
            if let wrapped = mirror.children.first {
                collect(label: label, value: wrapped.value, into: &out)
            }
        case .collection, .set:
            for (index, element) in mirror.children.prefix(maxElements).enumerated() {
                collect(label: "\(label)[\(index)]", value: element.value, into: &out)
            }
        case .dictionary:
            for element in mirror.children.prefix(maxElements) {
                let pair = Mirror(reflecting: element.value).children.map(\.value)
                if pair.count == 2 {
                    collect(label: "\(label)[\(pair[0])]", value: pair[1], into: &out)
                }
            }
        default:
            if type(of: value) is AnyClass {
                out.append(ObjectReference(label: label, target: value as AnyObject))
            }
        }
    }

    private static func ivarReferences(of object: AnyObject) -> [ObjectReference] {
        var out: [ObjectReference] = []
        var currentClass: AnyClass? = type(of: object)
        while let cls = currentClass, !isSystemClass(NSStringFromClass(cls)) {
            var count: UInt32 = 0
            if let ivars = class_copyIvarList(cls, &count) {
                for index in 0..<Int(count) {
                    let ivar = ivars[index]
                    guard let encoding = ivar_getTypeEncoding(ivar),
                          String(cString: encoding).hasPrefix("@"),
                          let target = object_getIvar(object, ivar) else {
                        continue
                    }
                    let name = ivar_getName(ivar).map { String(cString: $0) } ?? "ivar"
                    out.append(ObjectReference(label: name, target: target as AnyObject))
                }
                free(ivars)
            }
            currentClass = class_getSuperclass(cls)
        }
        return out
    }

    private static func isClosure(_ value: Any) -> Bool {
        var unwrapped = value
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .optional {
            guard let inner = mirror.children.first?.value else { return false }
            unwrapped = inner
        }
        return String(describing: type(of: unwrapped)).contains("->")
    }

    private static func retainers(of root: AnyObject, others: [(id: String, object: AnyObject)]) -> [JSONValue] {
        var rows: [JSONValue] = []
        for other in others where other.object !== root {
            for reference in references(of: other.object) where reference.target === root {
                rows.append(.object([
                    "id": .string(other.id),
                    "class": .string(typeName(other.object)),
                    "property": .string(reference.label)
                ]))
            }
        }
        return rows
    }

    private static func ownershipHints(_ root: AnyObject) -> [String] {
        #if canImport(UIKit)
        guard let controller = root as? UIViewController else { return [] }
        var hints: [String] = []
        if let parent = controller.parent {
            hints.append(
                "Still a child of \(typeName(parent)). Call willMove(toParent: nil), "
                    + "view.removeFromSuperview() and removeFromParent()."
            )
        }
        if let presenter = controller.presentingViewController, controller.isBeingDismissed == false {
            hints.append("Still presented by \(typeName(presenter)).")
        }
        if controller.viewIfLoaded?.window != nil {
            hints.append("Its view is still in a window.")
        }
        if let stack = controller.navigationController?.viewControllers, stack.contains(where: { $0 === controller }) {
            hints.append("Still in the navigation stack.")
        }
        return hints
        #else
        _ = root
        return []
        #endif
    }
}

/// Bounded depth-first walk from the leaked object over its stored references.
private struct RetentionWalk {
    let root: AnyObject
    var nodes: [JSONValue] = []
    var edges: [JSONValue] = []
    var cycles: [[String]] = []
    var hints: [String] = []
    var truncated = false
    private var seen: Set<ObjectIdentifier> = []

    init(root: AnyObject) {
        self.root = root
    }

    mutating func run() {
        addNode(root, depth: 0)
        visit(root, depth: 0, path: [RetentionInspector.typeName(root)])
    }

    private mutating func visit(_ object: AnyObject, depth: Int, path: [String]) {
        noteClosures(of: object, path: path, depth: depth)
        guard depth < RetentionInspector.maxDepth else { return }
        let from = RetentionInspector.objectId(object)
        for reference in RetentionInspector.references(of: object) {
            let target = reference.target
            let next = path + [reference.label]
            let closesCycle = target === root
            edges.append(.object([
                "from": .string(from),
                "to": .string(RetentionInspector.objectId(target)),
                "label": .string(reference.label),
                "cycle": .bool(closesCycle)
            ]))
            noteKnownRetainer(target, label: reference.label, path: path)
            if closesCycle {
                cycles.append(next + [RetentionInspector.typeName(root)])
                continue
            }
            guard !seen.contains(ObjectIdentifier(target)) else { continue }
            guard nodes.count < RetentionInspector.maxNodes else {
                truncated = true
                return
            }
            addNode(target, depth: depth + 1)
            if !RetentionInspector.isSystemClass(RetentionInspector.typeName(target)) {
                visit(target, depth: depth + 1, path: next + [RetentionInspector.typeName(target)])
            }
        }
    }

    private mutating func addNode(_ object: AnyObject, depth: Int) {
        seen.insert(ObjectIdentifier(object))
        let name = RetentionInspector.typeName(object)
        nodes.append(.object([
            "id": .string(RetentionInspector.objectId(object)),
            "class": .string(name),
            "depth": .number(Double(depth)),
            "root": .bool(object === root),
            "system": .bool(RetentionInspector.isSystemClass(name))
        ]))
    }

    private mutating func noteClosures(of object: AnyObject, path: [String], depth: Int) {
        guard depth <= 2 else { return }
        for label in RetentionInspector.closureLabels(of: object) {
            let owner = path.joined(separator: ".")
            hints.append("Closure \(owner).\(label) may capture self strongly. Use [weak self] in the closure.")
        }
    }

    private mutating func noteKnownRetainer(_ target: AnyObject, label: String, path: [String]) {
        let owner = path.joined(separator: ".")
        let name = RetentionInspector.typeName(target)
        if target is Timer {
            hints.append("Timer \(owner).\(label) retains its target until invalidate() is called.")
        } else if name.contains("DisplayLink") {
            hints.append("CADisplayLink \(owner).\(label) retains its target until invalidate() is called.")
        } else if name.contains("Observer") {
            hints.append("Observer token \(owner).\(label): its block may capture self. Remove the observer.")
        } else if label.lowercased().contains("delegate"), target === root {
            hints.append("\(owner).\(label) points back to the leaked object. Declare the delegate weak.")
        }
    }
}
#endif
