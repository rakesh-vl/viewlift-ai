#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// Longest edge of a live preview, in pixels. The Live tab is a small column, so a retina PNG is wasted work.
enum PreviewScale {
    static let maxPixel: CGFloat = 640

    static func pixels(width: CGFloat, height: CGFloat, maxPixel: CGFloat = PreviewScale.maxPixel) -> CGFloat {
        let longest = max(width, height)
        guard longest > 1, maxPixel > 1 else { return 1 }
        return min(1, maxPixel / longest)
    }
}

enum PreviewCapture {
    static let byteLimit = 140_000

    static func jpeg(maxBytes: Int = byteLimit) async -> [String: JSONValue]? {
        #if canImport(UIKit)
        return await capture(maxBytes: maxBytes)
        #else
        _ = maxBytes
        return nil
        #endif
    }

    #if canImport(UIKit)
    /// Small JPEG of the key window. Same draw as a still screenshot, without a layout pass.
    @MainActor
    static func capture(maxBytes: Int = byteLimit) -> [String: JSONValue]? {
        guard AutomationScreen.application?.applicationState == .active, let window = keyWindow() else {
            return nil
        }
        let size = window.bounds.size
        guard size.width > 1, size.height > 1 else { return nil }
        let scale = PreviewScale.pixels(width: size.width, height: size.height)
        guard let image = WindowSnapshot.image(of: window, scale: scale, prepare: false),
              let data = compressed(image, maxBytes: maxBytes) else {
            return nil
        }
        return [
            "mime": .string("image/jpeg"),
            "base64": .string(data.base64EncodedString()),
            "width": .number(Double(size.width)),
            "height": .number(Double(size.height))
        ]
    }

    private static func compressed(_ image: UIImage, maxBytes: Int) -> Data? {
        for quality in [0.55, 0.35, 0.2] as [CGFloat] {
            guard let data = image.jpegData(compressionQuality: quality), data.count <= maxBytes else {
                continue
            }
            return data
        }
        return nil
    }

    @MainActor
    private static func keyWindow() -> UIWindow? {
        AutomationScreen.keyWindow()
    }
    #endif
}
#endif
