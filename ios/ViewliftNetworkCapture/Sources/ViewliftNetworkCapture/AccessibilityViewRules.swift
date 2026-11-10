#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Rules that need the UIKit view itself: text contrast, Dynamic Type, and controls VoiceOver skips.
@MainActor
struct AccessibilityViewRules {
    private static let maxDepth = 80

    let window: UIWindow
    let reachable: Set<ObjectIdentifier>
    private(set) var findings: [AccessibilityFinding] = []

    static func check(window: UIWindow, nodes: [AccessibilityNode]) -> [AccessibilityFinding] {
        var rules = AccessibilityViewRules(window: window, reachable: Set(nodes.map { ObjectIdentifier($0.view) }))
        rules.walk(window, covered: false, depth: 0)
        return rules.findings
    }

    private mutating func walk(_ view: UIView, covered: Bool, depth: Int) {
        guard depth < Self.maxDepth, !view.isHidden, view.alpha > 0.01,
              !view.accessibilityElementsHidden, !UILayoutDebugService.isOverlay(view) else {
            return
        }
        let frame = view.convert(view.bounds, to: window)
        guard frame.width > 0, frame.height > 0, frame.intersects(window.bounds) else { return }
        let isCovered = covered || reachable.contains(ObjectIdentifier(view))
        if let label = view as? UILabel {
            findings += Self.textFindings(label, frame: frame)
        }
        if let control = view as? UIControl, control.isEnabled, !isCovered, !(view is UITextField) {
            findings.append(Self.attach(view, frame: frame, AccessibilityFinding(
                rule: "unreachable-control", severity: .warning,
                title: "VoiceOver cannot reach this \(Self.typeName(view))",
                detail: "It is on screen and enabled, but no accessibility element exposes it.",
                fix: "Set isAccessibilityElement = true and a label, or include it in accessibilityElements."
            )))
        }
        for child in view.subviews {
            walk(child, covered: isCovered, depth: depth + 1)
        }
    }

    private static func textFindings(_ label: UILabel, frame: CGRect) -> [AccessibilityFinding] {
        guard let text = label.text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return []
        }
        var out: [AccessibilityFinding] = []
        let preview = String(text.prefix(40))
        if let ratio = contrast(label) {
            let large = label.font.pointSize >= 18
                || (label.font.pointSize >= 14 && label.font.fontDescriptor.symbolicTraits.contains(.traitBold))
            let needed = large ? 3.0 : 4.5
            if ratio < needed {
                out.append(attach(label, frame: frame, AccessibilityFinding(
                    rule: "low-contrast", severity: .warning,
                    title: String(format: "Low text contrast %.1f:1 on \"%@\"", ratio, preview),
                    detail: String(
                        format: "%@ text needs at least %.1f:1 (WCAG AA).", large ? "Large" : "Body", needed
                    ),
                    fix: "Darken or lighten the text color or its background."
                )))
            }
        }
        #if os(iOS)
        if !label.adjustsFontForContentSizeCategory {
            out.append(attach(label, frame: frame, AccessibilityFinding(
                rule: "dynamic-type", severity: .info,
                title: "Text ignores the user's text size: \"\(preview)\"",
                detail: "The font stays the same when Larger Text is turned on.",
                fix: "Use UIFont.preferredFont(forTextStyle:) or UIFontMetrics, "
                    + "and set adjustsFontForContentSizeCategory = true."
            )))
        }
        #endif
        return out
    }

    /// Text color against the first opaque background behind the label, or nil when unknown.
    private static func contrast(_ label: UILabel) -> Double? {
        let traits = label.traitCollection
        guard let text = luminance(label.textColor.resolvedColor(with: traits)) else { return nil }
        var current: UIView? = label
        while let view = current {
            if let color = view.backgroundColor?.resolvedColor(with: traits), color.cgColor.alpha >= 0.95 {
                guard let back = luminance(color) else { return nil }
                return (max(text, back) + 0.05) / (min(text, back) + 0.05)
            }
            current = view.superview
        }
        return nil
    }

    private static func luminance(_ color: UIColor) -> Double? {
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let converted = color.cgColor.converted(to: space, intent: .defaultIntent, options: nil),
              let parts = converted.components, parts.count >= 3 else {
            return nil
        }
        let channel = { (value: CGFloat) -> Double in
            let component = Double(value)
            return component <= 0.039_28 ? component / 12.92 : pow((component + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(parts[0]) + 0.7152 * channel(parts[1]) + 0.0722 * channel(parts[2])
    }

    private static func typeName(_ view: UIView) -> String {
        String(describing: type(of: view))
    }

    private static func attach(_ view: UIView, frame: CGRect, _ base: AccessibilityFinding) -> AccessibilityFinding {
        var item = base
        item.role = typeName(view)
        item.label = view.accessibilityLabel ?? ""
        item.identifier = view.accessibilityIdentifier ?? ""
        item.frame = frame
        item.viewId = String(ObjectIdentifier(view).hashValue)
        return item
    }
}
#endif
#endif
