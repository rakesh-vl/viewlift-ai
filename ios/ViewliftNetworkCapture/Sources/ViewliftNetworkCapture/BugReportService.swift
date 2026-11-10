#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// `bug.capture`: a JPEG of the current screen plus the context a bug note needs.
/// The receiver drops events over 512 KB, so the image is shrunk until it fits.
enum BugReportService {
    static let imageBudget = 300_000

    @MainActor
    static func capture(performance: [String: JSONValue]) throws -> [String: JSONValue] {
        #if canImport(UIKit)
        guard let window = AutomationScreen.keyWindow() ?? AutomationScreen.windows().first,
              let image = jpeg(of: window, budget: imageBudget) else {
            throw DebugBridgeError.payloadTooLarge
        }
        let identity = AppIdentity.current(frameworks: [])
        let device = DeviceIdentity.current()
        return [
            "mime": .string("image/jpeg"),
            "base64": .string(image.data.base64EncodedString()),
            "width": .number(image.width),
            "height": .number(image.height),
            "screen": .string(AutomationScreen.name()),
            "captured_at": .number(Date().timeIntervalSince1970),
            "app": .object([
                "name": .string(identity.appName),
                "bundle_id": .string(identity.bundleId),
                "version": .string(identity.appVersion),
                "build": .string(identity.build)
            ]),
            "device": .object([
                "name": .string(device.deviceName),
                "model": .string(device.deviceModel),
                "system": .string("\(UIDevice.current.systemName) \(UIDevice.current.systemVersion)"),
                "simulator": .bool(device.isSimulator),
                "locale": .string(Locale.current.identifier)
            ]),
            "performance": .object(performance),
            "actions": .array(HangInteractionTrail.shared.recent(before: Date(), limit: 12))
        ]
        #else
        _ = performance
        throw DebugBridgeError.unsupportedCommand
        #endif
    }

    #if canImport(UIKit)
    /// Largest JPEG of the window that fits `budget` bytes. Sizes are in window points.
    @MainActor
    static func jpeg(
        of window: UIWindow,
        budget: Int,
        edges: [Double] = [1_600, 1_200, 900, 640]
    ) -> ScreenGrab? {
        let longest = max(window.bounds.width, window.bounds.height)
        guard longest > 1 else { return nil }
        let native = window.windowScene?.screen.scale ?? 1
        for edge in edges {
            let scale = min(native, CGFloat(edge) / longest)
            guard let image = WindowSnapshot.image(of: window, scale: scale) else { return nil }
            for quality in [0.75, 0.55] {
                if let data = image.jpegData(compressionQuality: quality), data.count <= budget {
                    let size = window.bounds.size
                    return ScreenGrab(data: data, width: Double(size.width), height: Double(size.height))
                }
            }
        }
        return nil
    }
    #endif
}
#endif
