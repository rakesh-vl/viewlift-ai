#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

enum WindowSnapshot {
    /// Copies the pixels already on screen. Call on the main thread.
    /// Not `@MainActor` because hang and leak reports call it synchronously.
    /// `afterScreenUpdates: true` inside this renderer waits for a pass that cannot finish,
    /// so the bitmap stays white except for layers cached already, such as the nav bar.
    static func image(of window: UIWindow, scale: CGFloat? = nil, prepare: Bool = true) -> UIImage? {
        let size = window.bounds.size
        guard size.width > 1, size.height > 1 else { return nil }
        if prepare {
            window.layoutIfNeeded()
        }
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale ?? window.windowScene?.screen.scale ?? 1
        format.opaque = true
        let rect = CGRect(origin: .zero, size: size)
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            if !window.drawHierarchy(in: rect, afterScreenUpdates: false) {
                window.layer.render(in: context.cgContext)
            }
        }
    }
}
#endif

struct ScreenGrab: Sendable {
    var data: Data
    var width: Double
    var height: Double
    var windowId = ""
    var sceneId = ""
}

enum UIInspectionService {
    @MainActor
    static func hierarchy() -> [String: JSONValue] {
        UIHierarchyCapture.capture()
    }

    @MainActor
    static func layers(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        #if canImport(UIKit)
        return UILayerSnapshot.page(payload)
        #else
        return ["layers": .array([]), "done": .bool(true)]
        #endif
    }

    @MainActor
    static func describe(id: String) -> [String: JSONValue] {
        #if canImport(UIKit)
        guard let view = findView(id: id) else {
            return ["found": .bool(false)]
        }
        return UIViewSheet.make(for: view)
        #else
        return ["found": .bool(false)]
        #endif
    }

    @MainActor
    static func screenshot(windowId: String = "", sceneId: String = "") async -> ScreenGrab? {
        #if canImport(UIKit)
        guard var window = AutomationScreen.window(windowId: windowId, sceneId: sceneId) else { return nil }
        #if os(iOS)
        if orientationMismatch(window) {
            await Task.yield()
            window = AutomationScreen.window(windowId: windowId, sceneId: sceneId) ?? window
        }
        #endif
        let longest = max(window.bounds.width, window.bounds.height)
        let native = window.windowScene?.screen.scale ?? 1
        let scale = longest > 1 ? min(native, 1200 / longest) : native
        guard let image = WindowSnapshot.image(of: window, scale: scale),
              let data = image.pngData() else {
            return nil
        }
        return ScreenGrab(
            data: data,
            width: Double(window.bounds.width),
            height: Double(window.bounds.height),
            windowId: AutomationScreen.windowId(window),
            sceneId: AutomationScreen.sceneId(window.windowScene)
        )
        #else
        return nil
        #endif
    }

    #if canImport(UIKit)
    #if os(iOS)
    @MainActor
    private static func orientationMismatch(_ window: UIWindow) -> Bool {
        guard let orientation = window.windowScene?.interfaceOrientation,
              orientation != .unknown else {
            return false
        }
        let landscape = orientation == .landscapeLeft || orientation == .landscapeRight
        return landscape != (window.bounds.width > window.bounds.height)
    }
    #endif
    #endif

    @MainActor
    static func focus() -> [String: JSONValue] {
        FocusDebugMonitor.shared.snapshot()
    }

    @MainActor
    static func visual(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        #if canImport(UIKit)
        return UILayoutDebugService.shared.apply(payload)
        #else
        _ = payload
        return ["found": .bool(false)]
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

    /// The receiver rejects events above 512 KB, so the audit result is size-budgeted.
    static let assetPayloadBudget = 480_000

    static func assetAudit() -> [String: JSONValue] {
        let bundles = [Bundle.main] + Bundle.allFrameworks
        var rows: [JSONValue] = []
        var seen = Set<String>()
        var truncated = false
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
                    "bundle": .string(bundle.bundleIdentifier ?? "main"),
                    "path": .string(url.path)
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
                    truncated = true
                    break
                }
                if rows.count % 50 == 0,
                   (try? JSONEncoder().encode(["assets": JSONValue.array(rows)] as [String: JSONValue]).count) ?? assetPayloadBudget > assetPayloadBudget {
                    truncated = true
                    break
                }
            }
            if truncated { break }
        }
        return ["assets": .array(rows), "truncated": .bool(truncated)]
    }

    #if canImport(UIKit)
    @MainActor
    static func findView(id: String) -> UIView? {
        let windows = AutomationScreen.allWindows()
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
