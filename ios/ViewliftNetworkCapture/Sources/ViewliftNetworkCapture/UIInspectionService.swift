#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

enum UIInspectionService {
    @MainActor
    static func hierarchy() -> [String: JSONValue] {
        #if canImport(UIKit)
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        return [
            "windows": .array(windows.map { describe($0, depth: 0) }),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
        #else
        return ["windows": .array([])]
        #endif
    }

    @MainActor
    static func accessibilityAudit() -> [String: JSONValue] {
        #if canImport(UIKit)
        var findings: [JSONValue] = []
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        for window in windows {
            audit(view: window, path: className(window), findings: &findings)
        }
        return [
            "findings": .array(findings),
            "count": .number(Double(findings.count))
        ]
        #else
        return ["findings": .array([]), "count": .number(0)]
        #endif
    }

    @MainActor
    static func screenshot() -> Data? {
        #if canImport(UIKit)
        guard let window = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .flatMap(\.windows)
            .first(where: \.isKeyWindow) else {
            return nil
        }
        let renderer = UIGraphicsImageRenderer(bounds: window.bounds)
        return renderer.pngData { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: false)
        }
        #else
        return nil
        #endif
    }

    @MainActor
    static func highlight(id: String) -> [String: JSONValue] {
        #if canImport(UIKit)
        guard let view = findView(id: id) else {
            return ["found": .bool(false)]
        }
        let previous = view.layer.borderWidth
        let previousColor = view.layer.borderColor
        view.layer.borderWidth = 3
        view.layer.borderColor = UIColor.systemRed.cgColor
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak view] in
            view?.layer.borderWidth = previous
            view?.layer.borderColor = previousColor
        }
        return ["found": .bool(true)]
        #else
        return ["found": .bool(false)]
        #endif
    }

    @MainActor
    static func edit(id: String, payload: [String: JSONValue]) -> [String: JSONValue] {
        #if canImport(UIKit)
        guard let view = findView(id: id) else {
            return ["found": .bool(false)]
        }
        if case let .number(alpha) = payload["alpha"] {
            view.alpha = CGFloat(max(0, min(1, alpha)))
        }
        if case let .bool(hidden) = payload["hidden"] {
            view.isHidden = hidden
        }
        return ["found": .bool(true), "updated": .bool(true)]
        #else
        return ["found": .bool(false)]
        #endif
    }

    static func assetAudit() -> [String: JSONValue] {
        let bundles = [Bundle.main] + Bundle.allFrameworks
        var rows: [JSONValue] = []
        var seen = Set<String>()
        for bundle in bundles {
            guard let root = bundle.resourceURL,
                  let enumerator = FileManager.default.enumerator(
                    at: root,
                    includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey],
                    options: [.skipsHiddenFiles]
                  ) else {
                continue
            }
            for case let url as URL in enumerator {
                let ext = url.pathExtension.lowercased()
                guard ["png", "jpg", "jpeg", "heic", "webp", "pdf", "mp4", "mov"].contains(ext),
                      seen.insert(url.path).inserted else {
                    continue
                }
                let values = try? url.resourceValues(forKeys: [.fileSizeKey])
                var row: [String: JSONValue] = [
                    "name": .string(url.lastPathComponent),
                    "type": .string(ext),
                    "bytes": .number(Double(values?.fileSize ?? 0)),
                    "bundle": .string(bundle.bundleIdentifier ?? "main")
                ]
                #if canImport(UIKit)
                if let image = UIImage(contentsOfFile: url.path) {
                    row["width"] = .number(Double(image.size.width * image.scale))
                    row["height"] = .number(Double(image.size.height * image.scale))
                    row["estimated_memory_bytes"] = .number(
                        Double(image.size.width * image.scale * image.size.height * image.scale * 4)
                    )
                }
                #endif
                rows.append(.object(row))
                if rows.count >= 2_000 {
                    return ["assets": .array(rows), "truncated": .bool(true)]
                }
            }
        }
        return ["assets": .array(rows), "truncated": .bool(false)]
    }

    #if canImport(UIKit)
    @MainActor
    private static func describe(_ view: UIView, depth: Int) -> JSONValue {
        let frame = view.convert(view.bounds, to: nil)
        let constraints = view.constraints.prefix(100).map { constraint in
            JSONValue.object([
                "description": .string(constraint.description),
                "priority": .number(Double(constraint.priority.rawValue)),
                "constant": .number(Double(constraint.constant))
            ])
        }
        let children: [JSONValue]
        if depth >= 100 {
            children = []
        } else {
            children = view.subviews.prefix(500).map { describe($0, depth: depth + 1) }
        }
        return .object([
            "id": .string(String(ObjectIdentifier(view).hashValue)),
            "class": .string(className(view)),
            "x": .number(Double(frame.origin.x)),
            "y": .number(Double(frame.origin.y)),
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "alpha": .number(Double(view.alpha)),
            "hidden": .bool(view.isHidden),
            "accessibility_label": .string(view.accessibilityLabel ?? ""),
            "accessibility_identifier": .string(view.accessibilityIdentifier ?? ""),
            "constraints": .array(constraints),
            "children": .array(children)
        ])
    }

    @MainActor
    private static func audit(view: UIView, path: String, findings: inout [JSONValue]) {
        let current = "\(path)/\(className(view))"
        if view.isAccessibilityElement {
            if (view.accessibilityLabel ?? "").trimmingCharacters(in: .whitespaces).isEmpty {
                findings.append(finding("missing-label", current, "Accessibility element has no label"))
            }
            let size = view.bounds.size
            if size.width < 44 || size.height < 44 {
                findings.append(finding("small-target", current, "Touch target is smaller than 44x44"))
            }
        }
        if view.alpha > 0, view.alpha < 0.3, !view.isHidden {
            findings.append(finding("low-alpha", current, "Visible view has very low alpha"))
        }
        for child in view.subviews {
            audit(view: child, path: current, findings: &findings)
        }
    }

    private static func finding(_ kind: String, _ path: String, _ message: String) -> JSONValue {
        .object([
            "kind": .string(kind),
            "path": .string(path),
            "message": .string(message)
        ])
    }

    private static func className(_ value: AnyObject) -> String {
        NSStringFromClass(type(of: value))
    }

    @MainActor
    private static func findView(id: String) -> UIView? {
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
        for window in windows {
            if let match = findView(id: id, root: window) {
                return match
            }
        }
        return nil
    }

    private static func findView(id: String, root: UIView) -> UIView? {
        if String(ObjectIdentifier(root).hashValue) == id {
            return root
        }
        for child in root.subviews {
            if let match = findView(id: id, root: child) {
                return match
            }
        }
        return nil
    }
    #endif
}
#endif
