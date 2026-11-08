#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import SwiftUI
import UIKit

/// Renders a registered preview in an off-screen window so it draws with the device's fonts,
/// traits, and UIKit-backed SwiftUI controls, then returns a PNG.
@MainActor
enum PreviewRenderService {
    static let maxBytes = 480_000
    static let maxSide: CGFloat = 2_400

    struct Request {
        var dark: Bool
        var size: CGSize
        var scale: CGFloat

        init(_ payload: AutomationPayload, fallbackSize: CGSize) {
            dark = payload.string("color_scheme").lowercased() == "dark"
            let width = payload.number("width").map { CGFloat($0) } ?? fallbackSize.width
            let height = payload.number("height").map { CGFloat($0) } ?? fallbackSize.height
            size = CGSize(width: min(max(width, 20), maxSide), height: min(max(height, 20), maxSide))
            scale = CGFloat(min(max(payload.number("scale") ?? 2, 1), 3))
        }
    }

    static func render(_ payload: AutomationPayload) async throws -> [String: JSONValue] {
        let name = payload.string("name")
        guard let entry = PreviewRegistry.shared.entry(name) else {
            return [
                "ok": .bool(false),
                "detail": .string("No preview named \(name)."),
                "previews": PreviewRegistry.shared.list()["previews"] ?? .array([])
            ]
        }
        let requested = payload.string("state")
        let state = requested.isEmpty ? (entry.states.first ?? PreviewRegistry.defaultState) : requested
        guard entry.states.contains(state) else {
            return [
                "ok": .bool(false),
                "detail": .string("Unknown state \(state)."),
                "states": .array(entry.states.map(JSONValue.string))
            ]
        }
        let target = AutomationScreen.window(
            windowId: payload.string("window_id"),
            sceneId: payload.string("scene_id")
        )
        guard let scene = target?.windowScene else {
            return ["ok": .bool(false), "detail": .string("No active window scene to render in.")]
        }
        let request = Request(payload, fallbackSize: scene.coordinateSpace.bounds.size)
        let window = await draw(entry.make(state), request: request, in: scene)
        defer {
            window.isHidden = true
            window.rootViewController = nil
        }
        let png = try encode(window, scale: request.scale)
        return [
            "mime": .string("image/png"),
            "base64": .string(png.data.base64EncodedString()),
            "width": .number(Double(request.size.width)),
            "height": .number(Double(request.size.height)),
            "scale": .number(Double(png.scale)),
            "name": .string(entry.name),
            "state": .string(state),
            "color_scheme": .string(request.dark ? "dark" : "light")
        ]
    }

    private static func draw(_ content: AnyView, request: Request, in scene: UIWindowScene) async -> UIWindow {
        let host = UIHostingController(rootView: content)
        host.overrideUserInterfaceStyle = request.dark ? .dark : .light
        let window = UIWindow(windowScene: scene)
        // Off the visible screen so the user does not see the render.
        window.frame = CGRect(origin: CGPoint(x: -request.size.width - 200, y: 0), size: request.size)
        window.windowLevel = UIWindow.Level(rawValue: -1_000)
        window.rootViewController = host
        window.isHidden = false
        window.layoutIfNeeded()
        // One run-loop turn lets SwiftUI finish its first layout pass and resolve fonts.
        try? await Task.sleep(nanoseconds: 80_000_000)
        host.view.frame = CGRect(origin: .zero, size: request.size)
        host.view.layoutIfNeeded()
        return window
    }

    /// Halves the scale until the PNG fits in one debug event.
    private static func encode(_ view: UIView, scale: CGFloat) throws -> (data: Data, scale: CGFloat) {
        var current = scale
        while true {
            if let data = snapshot(view, scale: current), data.count <= maxBytes {
                return (data, current)
            }
            guard current > 1 else { throw DebugBridgeError.payloadTooLarge }
            current = max(1, current / 2)
        }
    }

    private static func snapshot(_ view: UIView, scale: CGFloat) -> Data? {
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = true
        let image = UIGraphicsImageRenderer(bounds: view.bounds, format: format).image { context in
            if !view.drawHierarchy(in: view.bounds, afterScreenUpdates: true) {
                view.layer.render(in: context.cgContext)
            }
        }
        return image.pngData()
    }
}
#endif
#endif
