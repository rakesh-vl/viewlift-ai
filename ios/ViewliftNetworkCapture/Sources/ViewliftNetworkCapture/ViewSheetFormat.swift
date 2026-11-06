#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

struct ViewSheetSection {
    let title: String
    var rows: [(String, String)]

    var json: JSONValue {
        .object([
            "title": .string(title),
            "rows": .array(rows.map { label, value in
                .object(["label": .string(label), "value": .string(value)])
            })
        ])
    }
}

enum ViewSheetFormat {
    static func point(_ value: CGFloat) -> String {
        let rounded = (value * 10).rounded() / 10
        if rounded == rounded.rounded() { return String(Int(rounded)) }
        return String(format: "%.1f", Double(rounded))
    }

    static func clip(_ text: String, limit: Int = 160) -> String {
        String(text.prefix(limit))
    }

    static func colorHex(_ color: UIColor?, traits: UITraitCollection) -> String? {
        guard let resolved = color?.resolvedColor(with: traits) else { return nil }
        var red: CGFloat = 0
        var green: CGFloat = 0
        var blue: CGFloat = 0
        var alpha: CGFloat = 0
        guard resolved.getRed(&red, green: &green, blue: &blue, alpha: &alpha), alpha > 0.01 else {
            return nil
        }
        let channels = [red, green, blue, alpha].map { Int((max(0, min(1, $0)) * 255).rounded()) }
        return "#" + channels.map { String(format: "%02X", $0) }.joined()
    }

    static func contentMode(_ mode: UIView.ContentMode) -> String {
        let names: [UIView.ContentMode: String] = [
            .scaleToFill: "Scale to fill",
            .scaleAspectFit: "Aspect fit",
            .scaleAspectFill: "Aspect fill",
            .redraw: "Redraw",
            .center: "Center",
            .top: "Top",
            .bottom: "Bottom",
            .left: "Left",
            .right: "Right",
            .topLeft: "Top left",
            .topRight: "Top right",
            .bottomLeft: "Bottom left",
            .bottomRight: "Bottom right"
        ]
        return names[mode] ?? "Other"
    }

    static func alignment(_ alignment: NSTextAlignment) -> String {
        switch alignment {
        case .left: return "Left"
        case .center: return "Center"
        case .right: return "Right"
        case .justified: return "Justified"
        case .natural: return "Natural"
        @unknown default: return "Other"
        }
    }

    static func traits(_ traits: UIAccessibilityTraits) -> String {
        let pairs: [(UIAccessibilityTraits, String)] = [
            (.button, "Button"), (.link, "Link"), (.header, "Header"), (.image, "Image"),
            (.selected, "Selected"), (.staticText, "Text"), (.adjustable, "Adjustable"),
            (.updatesFrequently, "Updates often"), (.notEnabled, "Not enabled")
        ]
        let names = pairs.compactMap { traits.contains($0.0) ? $0.1 : nil }
        return names.isEmpty ? "None" : names.joined(separator: ", ")
    }
}
#endif
#endif
