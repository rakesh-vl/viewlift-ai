#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Walks windows into `ui.hierarchy` nodes the way Xcode's view debugger lists them:
/// view controllers wrap their root views, SwiftUI hosts expand into their SwiftUI view tree,
/// and UIKit views hosted by SwiftUI sit under the SwiftUI node that draws them.
@MainActor
final class HierarchyWalker {
    private struct Context {
        var ancestorIsHost = false
        var ancestorSwiftUI = ""
        /// True below a host whose SwiftUI tree came from debug data, so accessibility nodes are not repeated.
        var swiftUITreeKnown = false
    }

    private(set) var remaining: Int
    private let includeConstraints: Bool
    private let maxDepth = 100
    private var seenControls = Set<String>()
    private(set) var registry: [UIView] = []

    init(budget: Int, includeConstraints: Bool) {
        remaining = budget
        self.includeConstraints = includeConstraints
    }

    func window(_ window: UIWindow, isKey: Bool) -> JSONValue {
        var node = describe(window, depth: 0, context: Context())
        if case var .object(fields) = node {
            fields["key_window"] = .bool(isKey)
            fields["window_id"] = .string(AutomationScreen.windowId(window))
            fields["scene_id"] = .string(AutomationScreen.sceneId(window.windowScene))
            let screen = AutomationScreen.screenFrame(window.bounds, in: window)
            fields["screen_frame"] = .object([
                "x": .number(Double(screen.origin.x)),
                "y": .number(Double(screen.origin.y)),
                "width": .number(Double(screen.width)),
                "height": .number(Double(screen.height))
            ])
            node = .object(fields)
        }
        return node
    }

    private func describe(_ view: UIView, depth: Int, context: Context) -> JSONValue {
        remaining -= 1
        registry.append(view)
        let frame = view.convert(view.bounds, to: nil)
        let marks = UIViewSheet.marks(
            for: view,
            ancestorIsHost: context.ancestorIsHost,
            ancestorSwiftUI: context.ancestorSwiftUI
        )
        let children = childNodes(of: view, depth: depth, marks: marks, context: context)
        let structural = UITypeName.displayName(uikit: marks.short, swiftui: marks.swiftui, role: marks.role)
        let display = HierarchyNodeFields.spokenName(of: view, marks: marks) ?? structural
        let viewId = String(ObjectIdentifier(view).hashValue)
        var fields: [String: JSONValue] = [
            "id": .string(viewId),
            "class": .string(marks.short),
            "display": .string(display),
            "module": .string(marks.module),
            "role": .string(marks.role),
            "x": .number(Double(frame.origin.x)),
            "y": .number(Double(frame.origin.y)),
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "alpha": .number(Double(view.alpha)),
            "hidden": .bool(view.isHidden),
            "clips": .bool(view.clipsToBounds),
            "corner_radius": .number(Double(view.layer.cornerRadius)),
            "accessibility_label": .string(view.accessibilityLabel ?? ""),
            "accessibility_identifier": .string(view.accessibilityIdentifier ?? ""),
            "children": .array(children)
        ]
        if !marks.controller.isEmpty { fields["controller"] = .string(marks.controller) }
        if !marks.swiftui.isEmpty { fields["swiftui"] = .string(marks.swiftui) }
        if marks.typeName != marks.short { fields["type"] = .string(marks.typeName) }
        fields.merge(HierarchyNodeFields.visuals(of: view)) { current, _ in current }
        fields.merge(UILayoutExplorer.compact(for: view)) { current, _ in current }
        if UILayoutExplorer.isAppOwned(module: marks.module, typeName: marks.short) {
            fields["app"] = .bool(true)
        }
        if includeConstraints {
            fields["constraints"] = .array(HierarchyNodeFields.constraints(of: view))
        }
        return wrapInController(.object(fields), view: view, viewId: viewId, role: marks.role)
    }

    /// Xcode lists a view controller as the parent of its root view. Same frame, no own layer.
    private func wrapInController(_ node: JSONValue, view: UIView, viewId: String, role: String) -> JSONValue {
        guard let controller = view.next as? UIViewController, controller.viewIfLoaded === view else {
            return node
        }
        let described = String(describing: type(of: controller))
        let qualified = String(reflecting: type(of: controller))
        let split = UITypeName.moduleAndShort(qualified: qualified, described: described)
        let isHost = UITypeName.isSwiftUIHost(described) || UITypeName.isSwiftUIHost(qualified)
        let name = isHost && split.short.contains("<") ? "Hosting View Controller" : split.short
        let frame = view.convert(view.bounds, to: nil)
        var fields: [String: JSONValue] = [
            "id": .string("vc-\(ObjectIdentifier(controller).hashValue)"),
            "class": .string(name),
            "display": .string(name),
            "module": .string(split.module),
            "role": .string(role),
            "kind": .string("controller"),
            "synthetic": .bool(true),
            "describe_id": .string(viewId),
            "x": .number(Double(frame.origin.x)),
            "y": .number(Double(frame.origin.y)),
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "alpha": .number(1),
            "hidden": .bool(view.isHidden),
            "children": .array([node])
        ]
        if qualified != split.short { fields["type"] = .string(String(qualified.prefix(160))) }
        if UILayoutExplorer.isAppOwned(module: split.module, typeName: split.short) {
            fields["app"] = .bool(true)
        }
        return .object(fields)
    }

    private func childNodes(of view: UIView, depth: Int, marks: UIViewSheet.Marks, context: Context) -> [JSONValue] {
        guard depth < maxDepth else { return [] }
        let hostName = marks.childInsideHost
            ? (marks.swiftui.isEmpty ? context.ancestorSwiftUI : marks.swiftui)
            : ""
        let selfIsHost = marks.role == "swiftui"
        let debugTree = selfIsHost ? SwiftUIHostTree.debugNodes(of: view) : nil
        let childContext = Context(
            ancestorIsHost: marks.childInsideHost,
            ancestorSwiftUI: hostName,
            swiftUITreeKnown: debugTree != nil || (context.swiftUITreeKnown && !selfIsHost)
        )
        var hosted: [SwiftUIHostTree.HostedChild] = []
        for child in view.subviews where remaining > 0 && !UILayoutDebugService.isOverlay(child) {
            let node = describe(child, depth: depth + 1, context: childContext)
            hosted.append(SwiftUIHostTree.HostedChild(frame: child.convert(child.bounds, to: nil), node: node))
        }
        if let debugTree, remaining > 0 {
            let hostContext = SwiftUIHostTree.Context(
                host: view,
                hostName: hostName,
                hostId: String(ObjectIdentifier(view).hashValue),
                labels: SwiftUIControlNodes.labels(in: view)
            )
            return SwiftUIHostTree.merge(debugTree, context: hostContext, hosted: hosted, remaining: &remaining)
        }
        var children = hosted.map(\.node)
        let insideSwiftUI = marks.role == "swiftui" || marks.childInsideHost
        guard insideSwiftUI, !childContext.swiftUITreeKnown, remaining > 0 else { return children }
        children.append(contentsOf: SwiftUIControlNodes.nodes(
            in: view,
            hostName: hostName,
            remaining: &remaining,
            seen: &seenControls
        ))
        return children
    }
}

/// Per-view fields shared by the walker: visuals, text, and constraints.
@MainActor
enum HierarchyNodeFields {
    static let constraintsPerView = 12

    static func spokenName(of view: UIView, marks: UIViewSheet.Marks) -> String? {
        let raw = displayedText(of: view) ?? view.accessibilityLabel ?? view.accessibilityValue
        let spoken = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !spoken.isEmpty else { return nil }
        let control = ["button", "label", "text"].contains(kind(of: view))
        guard marks.role != "uikit" || control else { return nil }
        let clipped = String(spoken.prefix(80))
        return clipped == marks.short ? nil : clipped
    }

    static func visuals(of view: UIView) -> [String: JSONValue] {
        var fields: [String: JSONValue] = [:]
        let background = view.backgroundColor?.resolvedColor(with: view.traitCollection)
            ?? view.layer.backgroundColor.map { UIColor(cgColor: $0) }
        if let hex = background.flatMap(hexString) {
            fields["background"] = .string(hex)
        }
        if view.layer.borderWidth > 0, let border = view.layer.borderColor.map({ UIColor(cgColor: $0) }),
           let hex = hexString(border) {
            fields["border_width"] = .number(Double(view.layer.borderWidth))
            fields["border_color"] = .string(hex)
        }
        if let text = displayedText(of: view), !text.isEmpty {
            fields["text"] = .string(String(text.prefix(160)))
        }
        fields["kind"] = .string(kind(of: view))
        return fields
    }

    static func constraints(of view: UIView) -> [JSONValue] {
        view.constraints.prefix(constraintsPerView).map { constraint in
            .object([
                "description": .string(String(constraint.description.prefix(200))),
                "priority": .number(Double(constraint.priority.rawValue)),
                "constant": .number(Double(constraint.constant))
            ])
        }
    }

    static func displayedText(of view: UIView) -> String? {
        switch view {
        case let label as UILabel:
            return label.text
        case let button as UIButton:
            return button.currentTitle
        case let field as UITextField:
            return field.text?.isEmpty == false ? field.text : field.placeholder
        case let textView as UITextView:
            return textView.text
        default:
            return nil
        }
    }

    static func kind(of view: UIView) -> String {
        switch view {
        case is UIWindow: return "window"
        case is UIImageView: return "image"
        case is UILabel: return "label"
        case is UIButton: return "button"
        case is UITextField, is UITextView: return "text"
        case is UIScrollView: return "scroll"
        case is UIControl: return "control"
        default: return "view"
        }
    }

    static func hexString(_ color: UIColor) -> String? {
        var red: CGFloat = 0
        var green: CGFloat = 0
        var blue: CGFloat = 0
        var alpha: CGFloat = 0
        guard color.getRed(&red, green: &green, blue: &blue, alpha: &alpha), alpha > 0.01 else {
            return nil
        }
        let channels = [red, green, blue, alpha].map { Int((max(0, min(1, $0)) * 255).rounded()) }
        return "#" + channels.map { String(format: "%02X", $0) }.joined()
    }
}
#endif
#endif
