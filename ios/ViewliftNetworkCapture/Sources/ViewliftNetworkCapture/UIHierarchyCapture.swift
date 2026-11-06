#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// Builds the `ui.hierarchy` payload: the view tree with visual attributes plus a
/// compressed key-window screenshot the dashboard maps onto each 3D layer.
/// The receiver rejects events above 512 KB, so the whole result is size-budgeted.
enum UIHierarchyCapture {
    static let payloadBudget = 480_000
    static let nodeBudget = 1_200
    static let constraintsPerView = 12

    @MainActor
    static func capture() -> [String: JSONValue] {
        #if canImport(UIKit)
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .filter { !$0.isHidden }
        let key = windows.first(where: \.isKeyWindow) ?? windows.first
        var result = tree(windows: windows, key: key, includeConstraints: true)
        var used = encodedSize(result)
        if used > payloadBudget {
            result = tree(windows: windows, key: key, includeConstraints: false)
            used = encodedSize(result)
        }
        if let key, let screenshot = screenshot(of: key, budget: payloadBudget - used) {
            result["screenshot"] = screenshot
        }
        return result
        #else
        return ["windows": .array([])]
        #endif
    }

    private static func encodedSize(_ value: [String: JSONValue]) -> Int {
        (try? JSONEncoder().encode(value).count) ?? Int.max
    }

    #if canImport(UIKit)
    @MainActor
    private static func tree(
        windows: [UIWindow],
        key: UIWindow?,
        includeConstraints: Bool
    ) -> [String: JSONValue] {
        var remaining = nodeBudget
        var nodes: [JSONValue] = []
        for window in windows {
            var node = describe(
                window,
                depth: 0,
                remaining: &remaining,
                includeConstraints: includeConstraints,
                ancestorIsHost: false
            )
            if case var .object(fields) = node {
                fields["key_window"] = .bool(window === key)
                node = .object(fields)
            }
            nodes.append(node)
        }
        return [
            "windows": .array(nodes),
            "truncated": .bool(remaining <= 0),
            "constraints_included": .bool(includeConstraints),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
    }

    @MainActor
    private static func describe(
        _ view: UIView,
        depth: Int,
        remaining: inout Int,
        includeConstraints: Bool,
        ancestorIsHost: Bool
    ) -> JSONValue {
        remaining -= 1
        let frame = view.convert(view.bounds, to: nil)
        let marks = UIViewSheet.marks(for: view, ancestorIsHost: ancestorIsHost)
        var children: [JSONValue] = []
        if depth < 100 {
            for child in view.subviews where remaining > 0 {
                children.append(
                    describe(
                        child,
                        depth: depth + 1,
                        remaining: &remaining,
                        includeConstraints: includeConstraints,
                        ancestorIsHost: marks.childInsideHost
                    )
                )
            }
        }
        var fields: [String: JSONValue] = [
            "id": .string(String(ObjectIdentifier(view).hashValue)),
            "class": .string(marks.short),
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
        fields.merge(visuals(of: view)) { current, _ in current }
        if includeConstraints {
            fields["constraints"] = .array(view.constraints.prefix(constraintsPerView).map { constraint in
                .object([
                    "description": .string(String(constraint.description.prefix(200))),
                    "priority": .number(Double(constraint.priority.rawValue)),
                    "constant": .number(Double(constraint.constant))
                ])
            })
        }
        return .object(fields)
    }

    @MainActor
    private static func visuals(of view: UIView) -> [String: JSONValue] {
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

    @MainActor
    private static func displayedText(of view: UIView) -> String? {
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

    @MainActor
    private static func kind(of view: UIView) -> String {
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

    private static func hexString(_ color: UIColor) -> String? {
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

    @MainActor
    private static func screenshot(of window: UIWindow, budget: Int) -> JSONValue? {
        // Base64 inflates the JPEG by 4/3; keep headroom for the wrapping JSON object.
        let byteLimit = (budget - 256) * 3 / 4
        guard byteLimit > 0, window.bounds.width > 0, window.bounds.height > 0 else { return nil }
        for scale in [2.0, 1.0] as [CGFloat] {
            let format = UIGraphicsImageRendererFormat()
            format.scale = scale
            format.opaque = true
            let image = UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in
                window.drawHierarchy(in: window.bounds, afterScreenUpdates: false)
            }
            for quality in [0.6, 0.4, 0.25] as [CGFloat] {
                guard let data = image.jpegData(compressionQuality: quality), data.count <= byteLimit else {
                    continue
                }
                return .object([
                    "mime": .string("image/jpeg"),
                    "base64": .string(data.base64EncodedString()),
                    "width": .number(Double(window.bounds.width)),
                    "height": .number(Double(window.bounds.height)),
                    "x": .number(Double(window.frame.origin.x)),
                    "y": .number(Double(window.frame.origin.y))
                ])
            }
        }
        return nil
    }
    #endif
}
#endif
