#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// Builds the `ui.hierarchy` payload: the view tree with visual attributes plus a
/// compressed key-window screenshot the dashboard falls back to when a layer has no own snapshot.
/// The receiver rejects events above 512 KB, so the whole result is size-budgeted.
enum UIHierarchyCapture {
    static let payloadBudget = 480_000
    static let nodeBudget = 2_500

    @MainActor
    static func capture() -> [String: JSONValue] {
        #if canImport(UIKit)
        let windows = AutomationScreen.allWindows()
            .filter { !$0.isHidden }
        let key = windows.first(where: \.isKeyWindow) ?? windows.first
        var result = tree(windows: windows, key: key, includeConstraints: true)
        var used = encodedSize(result)
        if used > payloadBudget {
            result = tree(windows: windows, key: key, includeConstraints: false)
            used = encodedSize(result)
        }
        let remaining = used < payloadBudget ? payloadBudget - used : 0
        if let key, let screenshot = screenshot(of: key, budget: remaining) {
            result["screenshot"] = screenshot
        }
        return result
        #else
        return ["windows": .array([])]
        #endif
    }

    private static func encodedSize(_ value: [String: JSONValue]) -> Int {
        // A failed encode is "no room left", not Int.max. Subtracting that sentinel overflows.
        (try? JSONEncoder().encode(value).count) ?? payloadBudget
    }

    /// Raw JPEG byte cap for a JSON budget. Base64 expands by 4/3.
    /// Returns 0 when the budget cannot hold a screenshot. Never traps.
    static func jpegByteLimit(for budget: Int) -> Int {
        guard budget > 256 else { return 0 }
        return (budget - 256) / 4 * 3
    }

    #if canImport(UIKit)
    @MainActor
    private static func tree(
        windows: [UIWindow],
        key: UIWindow?,
        includeConstraints: Bool
    ) -> [String: JSONValue] {
        let walker = HierarchyWalker(budget: nodeBudget, includeConstraints: includeConstraints)
        let nodes = windows.map { walker.window($0, isKey: $0 === key) }
        let keyViews = walker.registry.filter { key == nil || $0 === key || $0.window === key }
        UILayerSnapshot.register(keyViews)
        return [
            "windows": .array(nodes),
            "truncated": .bool(walker.remaining <= 0),
            "constraints_included": .bool(includeConstraints),
            "layers_available": .number(Double(keyViews.count)),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
    }

    @MainActor
    private static func screenshot(of window: UIWindow, budget: Int) -> JSONValue? {
        let byteLimit = jpegByteLimit(for: budget)
        guard byteLimit > 0, window.bounds.width > 0, window.bounds.height > 0 else { return nil }
        for scale in [2.0, 1.0] as [CGFloat] {
            guard let image = WindowSnapshot.image(of: window, scale: scale) else { return nil }
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
