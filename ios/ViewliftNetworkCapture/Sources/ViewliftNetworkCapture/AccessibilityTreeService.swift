#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// One on-screen accessibility element and the objects that answer actions for it.
struct AccessibilityNode {
    let object: NSObject
    /// The element itself when it is a view, otherwise the view that contains it.
    let view: UIView
    let isView: Bool
    let info: AccessibilityElementInfo
}

/// Reads the accessibility tree VoiceOver uses, so agents find elements by meaning, not coordinates.
/// SwiftUI elements come through the hosting view's accessibility container.
@MainActor
enum AccessibilityTreeService {
    static let defaultLimit = 300
    static let maxLimit = 1_000
    static let maxWait: Double = 10

    static func tree(_ payload: AutomationPayload) -> [String: JSONValue] {
        let limit = payload.int("limit", fallback: defaultLimit, range: 1...maxLimit)
        let query = ElementQuery(payload: payload)
        let all = nodes()
        let matched = query.isEmpty ? all : all.filter { query.matches($0.info) }
        let shown = matched.prefix(limit)
        return [
            "screen": .string(AutomationScreen.name()),
            "elements": .array(shown.map { $0.info.json() }),
            "count": .number(Double(matched.count)),
            "truncated": .bool(matched.count > shown.count),
            "focused": all.first { $0.info.focused }?.info.json() ?? .null
        ]
    }

    static func waitFor(_ payload: AutomationPayload) async -> [String: JSONValue] {
        let query = ElementQuery(payload: payload)
        guard !query.isEmpty else {
            return ["ok": .bool(false), "detail": .string("Pass label, identifier, text, role, or view_id.")]
        }
        let gone = payload.bool("gone")
        let timeout = min(max(payload.number("timeout") ?? 5, 0.2), maxWait)
        let started = Date()
        while true {
            let match = nodes().first { query.matches($0.info) }
            let waited = Date().timeIntervalSince(started)
            if (match != nil) != gone {
                return [
                    "found": .bool(match != nil),
                    "element": match?.info.json() ?? .null,
                    "waited_ms": .number((waited * 1_000).rounded())
                ]
            }
            if waited >= timeout {
                return [
                    "ok": .bool(false),
                    "found": .bool(match != nil),
                    "detail": .string("timed out"),
                    "waited_ms": .number((waited * 1_000).rounded())
                ]
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
    }

    /// The `index`-th element matching the query, or the view named by `view_id`.
    static func resolve(_ query: ElementQuery) -> AccessibilityNode? {
        guard !query.isEmpty else { return nil }
        let matches = nodes().filter { query.matches($0.info) }
        if matches.indices.contains(query.index) {
            return matches[query.index]
        }
        guard query.index == 0, !query.viewId.isEmpty, let view = UIInspectionService.findView(id: query.viewId),
              let window = view.window else {
            return nil
        }
        let semantics = view.isAccessibilityElement ? nil : UIKitSemantics.describe(view)
        return Collector.node(for: view, owner: view, window: window, focused: [], semantics: semantics)
    }

    /// Smallest element whose frame contains a window point.
    static func node(at point: CGPoint) -> AccessibilityNode? {
        nodes()
            .filter { $0.info.frame.contains(point) }
            .min { $0.info.frame.width * $0.info.frame.height < $1.info.frame.width * $1.info.frame.height }
    }

    static func nodes(limit: Int = maxLimit) -> [AccessibilityNode] {
        nodes(in: AutomationScreen.windows(), limit: limit)
    }

    static func nodes(in windows: [UIWindow], limit: Int = maxLimit) -> [AccessibilityNode] {
        let focused = Set(windows.compactMap { window -> ObjectIdentifier? in
            guard let item = UIFocusSystem.focusSystem(for: window)?.focusedItem as? NSObject else { return nil }
            return ObjectIdentifier(item)
        })
        var collector = Collector(limit: limit, focused: focused)
        for window in windows {
            collector.walk(window, window: window, depth: 0)
        }
        return collector.nodes
    }
}

@MainActor
private struct Collector {
    private static let maxDepth = 80
    private static let maxChildren = 500

    let limit: Int
    let focused: Set<ObjectIdentifier>
    private(set) var nodes: [AccessibilityNode] = []
    private var seen = Set<ObjectIdentifier>()

    init(limit: Int, focused: Set<ObjectIdentifier>) {
        self.limit = limit
        self.focused = focused
    }

    mutating func walk(_ view: UIView, window: UIWindow, depth: Int) {
        guard nodes.count < limit, depth < Self.maxDepth, Self.isVisible(view) else { return }
        if view.isAccessibilityElement {
            add(view, owner: view, window: window)
            return
        }
        let elements = Self.children(of: view)
        if !elements.isEmpty {
            for item in elements {
                visit(item, owner: view, window: window, depth: depth + 1)
            }
            return
        }
        if let semantics = UIKitSemantics.describe(view) {
            add(view, owner: view, window: window, semantics: semantics)
            if semantics.isLeaf { return }
        }
        for child in view.subviews {
            walk(child, window: window, depth: depth + 1)
        }
    }

    private mutating func visit(_ item: Any, owner: UIView, window: UIWindow, depth: Int) {
        if let view = item as? UIView {
            walk(view, window: window, depth: depth)
            return
        }
        guard let object = item as? NSObject, nodes.count < limit, depth < Self.maxDepth else { return }
        if object.isAccessibilityElement {
            add(object, owner: owner, window: window)
            return
        }
        for child in Self.children(of: object) {
            visit(child, owner: owner, window: window, depth: depth + 1)
        }
    }

    private mutating func add(
        _ object: NSObject,
        owner: UIView,
        window: UIWindow,
        semantics: UIKitSemantics? = nil
    ) {
        guard seen.insert(ObjectIdentifier(object)).inserted,
              let node = Self.node(
                for: object, owner: owner, window: window, focused: focused, semantics: semantics
              ) else {
            return
        }
        nodes.append(node)
    }

    /// `semantics` stands in for the label, value, and traits UIKit leaves empty without the
    /// accessibility runtime.
    static func node(
        for object: NSObject,
        owner: UIView,
        window: UIWindow,
        focused: Set<ObjectIdentifier>,
        semantics: UIKitSemantics? = nil
    ) -> AccessibilityNode? {
        let view = object as? UIView
        let frame = view.map { $0.convert($0.bounds, to: window) }
            ?? window.convert(object.accessibilityFrame, from: window.screen.coordinateSpace)
        guard frame.width > 0, frame.height > 0, frame.intersects(window.bounds) else { return nil }
        let traits = object.accessibilityTraits
        let role = AccessibilityRole.resolve(
            kind: AccessibilityTraitNames.kind(of: object),
            traits: AccessibilityTraitNames.names(traits).union(semantics?.traits ?? [])
        )
        let info = AccessibilityElementInfo(
            role: role,
            label: clean(semantics?.label ?? object.accessibilityLabel),
            identifier: AccessibilityTraitNames.identifier(of: object),
            value: clean(semantics?.value ?? object.accessibilityValue),
            frame: frame,
            enabled: !traits.contains(.notEnabled) && (view as? UIControl)?.isEnabled != false,
            focused: focused.contains(ObjectIdentifier(object)) || view?.isFirstResponder == true,
            selected: traits.contains(.selected),
            viewId: view.map { String(ObjectIdentifier($0).hashValue) } ?? "",
            windowId: AutomationScreen.windowId(window),
            sceneId: AutomationScreen.sceneId(window.windowScene),
            screenFrame: AutomationScreen.screenFrame(frame, in: window)
        )
        return AccessibilityNode(object: object, view: view ?? owner, isView: view != nil, info: info)
    }

    private static func children(of object: NSObject) -> [Any] {
        if let elements = object.accessibilityElements, !elements.isEmpty {
            return Array(elements.prefix(maxChildren))
        }
        let count = object.accessibilityElementCount()
        guard count > 0, count != NSNotFound else { return [] }
        return (0..<min(count, maxChildren)).compactMap { object.accessibilityElement(at: $0) }
    }

    private static func isVisible(_ view: UIView) -> Bool {
        !view.isHidden && view.alpha > 0.01 && !UILayoutDebugService.isOverlay(view)
    }

    private static func clean(_ raw: String?) -> String {
        String((raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines).prefix(200))
    }
}

/// Maps UIKit classes and traits to the names `AccessibilityRole` understands.
@MainActor
enum AccessibilityTraitNames {
    private static let traits: [(UIAccessibilityTraits, String)] = [
        (.button, "button"),
        (.link, "link"),
        (.searchField, "searchfield"),
        (.image, "image"),
        (.staticText, "statictext"),
        (.header, "header"),
        (.adjustable, "adjustable"),
        (.keyboardKey, "keyboardkey"),
        (.tabBar, "tabbar")
    ]

    static func names(_ value: UIAccessibilityTraits) -> Set<String> {
        var out = Set(traits.filter { value.contains($0.0) }.map(\.1))
        if #available(iOS 17.0, tvOS 17.0, *), value.contains(.toggleButton) {
            out.insert("toggle")
        }
        return out
    }

    static func kind(of object: NSObject) -> String {
        switch object {
        case let field as UITextField:
            return field.isSecureTextEntry ? "securetextfield" : "textfield"
        case let textView as UITextView:
            return TextInputFinder.isEditable(textView) ? "textview" : ""
        case is UISearchBar:
            return "searchfield"
        case is UISegmentedControl:
            return "segmentedcontrol"
        case is UITableViewCell, is UICollectionViewCell:
            return "cell"
        default:
            return platformKind(of: object)
        }
    }

    static func identifier(of object: NSObject) -> String {
        if let view = object as? UIView {
            return view.accessibilityIdentifier ?? ""
        }
        if let element = object as? UIAccessibilityIdentification {
            return element.accessibilityIdentifier ?? ""
        }
        let selector = NSSelectorFromString("accessibilityIdentifier")
        guard object.responds(to: selector) else { return "" }
        return object.perform(selector)?.takeUnretainedValue() as? String ?? ""
    }

    private static func platformKind(of object: NSObject) -> String {
        #if os(iOS)
        if object is UISwitch { return "switch" }
        if object is UISlider { return "slider" }
        #endif
        return ""
    }
}

#endif
#endif
