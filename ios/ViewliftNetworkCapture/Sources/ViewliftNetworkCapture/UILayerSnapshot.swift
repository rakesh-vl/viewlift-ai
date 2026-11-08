#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// `ui.layers`: one image per view with only that view's own pixels, like Xcode's 3D layers.
/// Direct subview layers are hidden inside one uncommitted transaction while the layer renders,
/// so the screen never shows the change. Results are paged to stay under the 512 KB event limit.
@MainActor
enum UILayerSnapshot {
    static let pageBudget = 420_000
    static let maxSide: CGFloat = 1_024
    static let pageTimeLimit: TimeInterval = 0.35

    private final class Entry {
        let id: String
        weak var view: UIView?

        init(view: UIView) {
            id = String(ObjectIdentifier(view).hashValue)
            self.view = view
        }
    }

    private enum Outcome {
        case image(JSONValue, bytes: Int)
        case empty
        case crop
        case missing
    }

    private static var entries: [Entry] = []

    /// Views from the last `ui.hierarchy`, in draw order.
    static func register(_ views: [UIView]) {
        entries = views.map(Entry.init)
    }

    static func page(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        let scale = min(3, max(0.5, number(payload["scale"]) ?? 1))
        let wanted = Set(strings(payload["ids"]))
        let pool = wanted.isEmpty ? entries : entries.filter { wanted.contains($0.id) }
        let start = max(0, Int(safe: number(payload["offset"]) ?? 0))
        var layers: [JSONValue] = []
        var empty: [JSONValue] = []
        var crop: [JSONValue] = []
        var missing: [JSONValue] = []
        var used = 0
        var index = start
        let began = Date()
        pageLoop: while index < pool.count {
            if !layers.isEmpty, Date().timeIntervalSince(began) > pageTimeLimit { break }
            let entry = pool[index]
            switch outcome(for: entry, scale: scale) {
            case let .image(layer, bytes):
                if used + bytes > pageBudget, !layers.isEmpty { break pageLoop }
                layers.append(layer)
                used += bytes
            case .empty:
                empty.append(.string(entry.id))
            case .crop:
                crop.append(.string(entry.id))
            case .missing:
                missing.append(.string(entry.id))
            }
            index += 1
        }
        return [
            "layers": .array(layers),
            "empty": .array(empty),
            "crop": .array(crop),
            "missing": .array(missing),
            "offset": .number(Double(start)),
            "next_offset": .number(Double(index)),
            "total": .number(Double(pool.count)),
            "done": .bool(index >= pool.count)
        ]
    }

    /// Halves the scale until one layer fits a page on its own.
    private static func outcome(for entry: Entry, scale: CGFloat) -> Outcome {
        guard let view = entry.view, view is UIWindow || view.window != nil else { return .missing }
        if drawsOffscreen(view) { return .crop }
        var attempt = scale
        for _ in 0..<3 {
            guard let shot = render(view, scale: attempt) else { return .empty }
            let encoded = shot.data.base64EncodedString()
            if encoded.count + 128 <= pageBudget {
                let layer: JSONValue = .object([
                    "id": .string(entry.id),
                    "mime": .string(shot.mime),
                    "base64": .string(encoded),
                    "width": .number(Double(view.bounds.width)),
                    "height": .number(Double(view.bounds.height))
                ])
                return .image(layer, bytes: encoded.count + 128)
            }
            attempt /= 2
        }
        return .crop
    }

    /// Video, Metal, and blur draw on the render server, so `render(in:)` returns nothing useful.
    private static func drawsOffscreen(_ view: UIView) -> Bool {
        if view is UIVisualEffectView { return true }
        let layerType = String(describing: type(of: view.layer))
        return ["AVPlayerLayer", "AVSampleBufferDisplayLayer", "CAMetalLayer", "CAEAGLLayer"]
            .contains { layerType.contains($0) }
    }

    private struct Shot {
        let data: Data
        let mime: String
    }

    private static func render(_ view: UIView, scale: CGFloat) -> Shot? {
        let bounds = view.bounds
        guard bounds.width >= 1, bounds.height >= 1 else { return nil }
        let longest = max(bounds.width, bounds.height) * scale
        let effective = longest > maxSide ? scale * maxSide / longest : scale
        // Rounding down keeps edge pixels fully covered, so opaque layers can use JPEG.
        let width = max(1, Int(safe: (bounds.width * effective).rounded(.down)))
        let height = max(1, Int(safe: (bounds.height * effective).rounded(.down)))
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }
        context.translateBy(x: 0, y: CGFloat(height))
        context.scaleBy(x: effective, y: -effective)
        context.translateBy(x: -bounds.minX, y: -bounds.minY)
        let covered = view.subviews.map(\.layer).filter { !$0.isHidden }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        covered.forEach { $0.isHidden = true }
        view.layer.render(in: context)
        covered.forEach { $0.isHidden = false }
        CATransaction.commit()
        let coverage = alphaCoverage(context, width: width, height: height)
        guard coverage.visible, let image = context.makeImage() else { return nil }
        let picture = UIImage(cgImage: image)
        if coverage.opaque, let jpeg = picture.jpegData(compressionQuality: 0.7) {
            return Shot(data: jpeg, mime: "image/jpeg")
        }
        guard let png = picture.pngData() else { return nil }
        return Shot(data: png, mime: "image/png")
    }

    private static func alphaCoverage(_ context: CGContext, width: Int, height: Int) -> (visible: Bool, opaque: Bool) {
        guard let base = context.data else { return (true, false) }
        let row = context.bytesPerRow
        let bytes = base.assumingMemoryBound(to: UInt8.self)
        var visible = false
        var opaque = true
        for line in 0..<height {
            let start = line * row
            for column in 0..<width {
                let alpha = bytes[start + column * 4 + 3]
                if alpha > 0 { visible = true }
                if alpha < 255 { opaque = false }
                if visible, !opaque { return (true, false) }
            }
        }
        return (visible, opaque)
    }

    private static func number(_ value: JSONValue?) -> CGFloat? {
        if case let .number(item) = value { return CGFloat(item) }
        return nil
    }

    private static func strings(_ value: JSONValue?) -> [String] {
        guard case let .array(items) = value else { return [] }
        return items.compactMap {
            if case let .string(item) = $0 { return item }
            return nil
        }
    }
}
#endif
#endif
