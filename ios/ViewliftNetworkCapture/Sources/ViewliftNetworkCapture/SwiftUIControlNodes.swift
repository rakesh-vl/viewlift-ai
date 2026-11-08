#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Buttons, text, and images SwiftUI draws inside a hosting view without making a `UIView`.
/// Those controls exist on the accessibility tree. `UIView.subviews` never lists them.
enum SwiftUIControlNodes {
    private static let maxDepth = 5
    private static let maxPerView = 80

    struct Label {
        let frame: CGRect
        let text: String
    }

    /// Spoken labels with window frames, used to name SwiftUI `Text` and `Button` debug nodes.
    @MainActor
    static func labels(in view: UIView) -> [Label] {
        var found: [Label] = []
        var visited = Set<ObjectIdentifier>()
        var pending = children(of: view).map { ($0, 0) }
        while let (element, depth) = pending.popLast(), found.count < maxPerView * 4 {
            guard visited.insert(ObjectIdentifier(element)).inserted, !(element is UIView) else { continue }
            let text = UITypeName.controlTitle(
                label: element.accessibilityLabel ?? "",
                value: element.accessibilityValue ?? "",
                fallback: ""
            )
            if !text.isEmpty {
                found.append(Label(frame: windowFrame(of: element, relativeTo: view), text: text))
            }
            if depth < maxDepth {
                pending.append(contentsOf: children(of: element).map { ($0, depth + 1) })
            }
        }
        return found
    }

    /// Shared state for one walk over a hosting view's accessibility elements.
    private struct Walk {
        let hostName: String
        let root: UIView
        var remaining: Int
        var seen: Set<String>
        var visited = Set<ObjectIdentifier>()
    }

    /// One drawn control: its title, inferred kind, and window frame.
    private struct Control {
        let title: String
        let kind: (name: String, kind: String)
        let frame: CGRect
    }

    @MainActor
    static func nodes(
        in view: UIView,
        hostName: String,
        remaining: inout Int,
        seen: inout Set<String>
    ) -> [JSONValue] {
        var produced: [JSONValue] = []
        var walk = Walk(hostName: hostName, root: view, remaining: remaining, seen: seen)
        for element in children(of: view) where walk.remaining > 0 && produced.count < maxPerView {
            append(element, depth: 0, walk: &walk, into: &produced)
        }
        remaining = walk.remaining
        seen = walk.seen
        return produced
    }

    @MainActor
    private static func append(
        _ element: NSObject,
        depth: Int,
        walk: inout Walk,
        into produced: inout [JSONValue]
    ) {
        let root = walk.root
        guard walk.visited.insert(ObjectIdentifier(element)).inserted, element !== root else { return }
        if element is UIView { return }
        let nested = depth < maxDepth ? children(of: element) : []
        var childNodes: [JSONValue] = []
        for child in nested where walk.remaining > 0 && childNodes.count < maxPerView {
            append(child, depth: depth + 1, walk: &walk, into: &childNodes)
        }
        let frame = windowFrame(of: element, relativeTo: root)
        let kind = traitKind(element.accessibilityTraits)
        let title = UITypeName.controlTitle(
            label: element.accessibilityLabel ?? "",
            value: element.accessibilityValue ?? "",
            fallback: kind.name
        )
        let geometry = [frame.minX, frame.minY, frame.width, frame.height].map { String(Int(safe: $0)) }
        let key = "\(title)|" + geometry.joined(separator: "|")
        guard walk.seen.insert(key).inserted else { return }
        if matchesSubview(frame, of: root) { return }
        if frame.width < 1, frame.height < 1, childNodes.isEmpty { return }
        if title == kind.name, childNodes.isEmpty, frame.width < 1 { return }
        guard walk.remaining > 0 else { return }
        walk.remaining -= 1
        if title == "Control", !childNodes.isEmpty {
            produced.append(contentsOf: childNodes)
            return
        }
        let control = Control(title: title, kind: kind, frame: frame)
        produced.append(node(element, control: control, hostName: walk.hostName, children: childNodes))
    }

    private static func node(
        _ element: NSObject,
        control: Control,
        hostName: String,
        children: [JSONValue]
    ) -> JSONValue {
        let (title, kind, frame) = (control.title, control.kind, control.frame)
        var fields: [String: JSONValue] = [
            "id": .string("ax-\(ObjectIdentifier(element).hashValue)"),
            "class": .string(kind.name),
            "display": .string(title),
            "role": .string("swiftui"),
            "kind": .string(kind.kind),
            "synthetic": .bool(true),
            "x": .number(Double(frame.minX)),
            "y": .number(Double(frame.minY)),
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "alpha": .number(1),
            "hidden": .bool(false),
            "text": .string(title == kind.name ? "" : title),
            "accessibility_label": .string(element.accessibilityLabel ?? ""),
            "accessibility_identifier": .string(identifier(of: element)),
            "children": .array(children)
        ]
        if !hostName.isEmpty { fields["swiftui"] = .string(hostName) }
        let value = element.accessibilityValue ?? ""
        if !value.isEmpty { fields["accessibility_value"] = .string(String(value.prefix(80))) }
        return .object(fields)
    }

    @MainActor
    private static func children(of object: NSObject) -> [NSObject] {
        if let listed = object.accessibilityElements, !listed.isEmpty {
            return listed.compactMap { $0 as? NSObject }
        }
        let count = object.accessibilityElementCount()
        guard count > 0, count <= maxPerView else { return [] }
        return (0..<count).compactMap { object.accessibilityElement(at: $0) as? NSObject }
    }

    @MainActor
    private static func windowFrame(of element: NSObject, relativeTo view: UIView) -> CGRect {
        let screen = element.accessibilityFrame
        guard screen.width > 0, screen.height > 0, let window = view.window else { return screen }
        return window.convert(screen, from: window.screen.coordinateSpace)
    }

    @MainActor
    private static func matchesSubview(_ frame: CGRect, of view: UIView) -> Bool {
        guard frame.width > 1, frame.height > 1 else { return false }
        return view.subviews.contains { child in
            let childFrame = child.convert(child.bounds, to: nil)
            return abs(childFrame.minX - frame.minX) < 4
                && abs(childFrame.minY - frame.minY) < 4
                && abs(childFrame.width - frame.width) < 6
                && abs(childFrame.height - frame.height) < 6
        }
    }

    private static func identifier(of element: NSObject) -> String {
        (element as? UIAccessibilityIdentification)?.accessibilityIdentifier ?? ""
    }

    private static func traitKind(_ traits: UIAccessibilityTraits) -> (name: String, kind: String) {
        if traits.contains(.button) { return ("Button", "button") }
        if traits.contains(.link) { return ("Link", "link") }
        if traits.contains(.searchField) { return ("Field", "text") }
        if traits.contains(.adjustable) { return ("Slider", "control") }
        if traits.contains(.image) { return ("Image", "image") }
        if traits.contains(.staticText) || traits.contains(.header) { return ("Text", "text") }
        return ("Control", "control")
    }
}
#endif
#endif
