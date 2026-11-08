#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Layout box, constraints, and stack flex for one view.
/// Compact fields ride on `ui.hierarchy`. The full explorer rides on `ui.describe`.
enum UILayoutExplorer {
    struct ImageLoad {
        let pixelWidth: Int
        let pixelHeight: Int
        let displayWidth: Int
        let displayHeight: Int
        let extraKilobytes: Int
    }

    @MainActor
    static func isAppOwned(module: String, typeName: String) -> Bool {
        let systemModules: Set<String> = [
            "UIKit", "UIKitCore", "SwiftUI", "SwiftUICore", "QuartzCore", "Foundation"
        ]
        if systemModules.contains(module) || typeName.hasPrefix("_") {
            return false
        }
        if module.isEmpty && (typeName.hasPrefix("UI") || typeName.hasPrefix("NS")) {
            return false
        }
        return true
    }

    @MainActor
    static func compact(for view: UIView) -> [String: JSONValue] {
        var fields: [String: JSONValue] = [:]
        if let padding = notablePadding(of: view) {
            fields["pad"] = numbers(padding)
        }
        if let baseline = baselineOffset(of: view) {
            fields["baseline"] = .number(Double(baseline))
        }
        if let image = imageLoad(of: view), image.extraKilobytes >= 128 {
            fields["pixels"] = numbers([CGFloat(image.pixelWidth), CGFloat(image.pixelHeight)])
            fields["extra_kb"] = .number(Double(image.extraKilobytes))
        }
        if let stack = view as? UIStackView {
            fields["stack_axis"] = .string(stack.axis == .vertical ? "vertical" : "horizontal")
        }
        let issues = issueCodes(for: view)
        if !issues.isEmpty {
            fields["issues"] = .array(issues.map { .string($0) })
        }
        return fields
    }

    @MainActor
    static func make(for view: UIView) -> [String: JSONValue] {
        var explorer: [String: JSONValue] = [
            "box": .object(box(for: view)),
            "constraints": .array(UILayoutSheet.constraints(for: view)),
            "issues": .array(issueRows(for: view))
        ]
        if let flex = UILayoutSheet.flex(for: view) {
            explorer["flex"] = .object(flex)
        }
        return explorer
    }

    @MainActor
    private static func box(for view: UIView) -> [String: JSONValue] {
        let frame = view.bounds
        let padding = physicalPadding(of: view)
        let border = view.layer.borderWidth
        let contentWidth = max(0, frame.width - padding[1] - padding[3] - border * 2)
        let contentHeight = max(0, frame.height - padding[0] - padding[2] - border * 2)
        var fields: [String: JSONValue] = [
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "margin": numbers(margin(of: view)),
            "border": .number(Double(border)),
            "padding": numbers(padding),
            "content_width": .number(Double(contentWidth)),
            "content_height": .number(Double(contentHeight))
        ]
        let safe = view.safeAreaInsets
        if safe != .zero {
            fields["safe_area"] = numbers([safe.top, safe.left, safe.bottom, safe.right])
        }
        return fields
    }

    @MainActor
    private static func issueRows(for view: UIView) -> [JSONValue] {
        var rows: [(String, String)] = []
        if view.hasAmbiguousLayout {
            rows.append(("ambiguous", "Layout is ambiguous. More than one constraint set can satisfy this view."))
        }
        if let message = overflowMessage(of: view) {
            rows.append(("overflow", message))
        }
        if let image = imageLoad(of: view), image.extraKilobytes >= 128 {
            let decoded = "\(image.pixelWidth)×\(image.pixelHeight)"
            let shown = "\(image.displayWidth)×\(image.displayHeight)"
            let extra = "About \(image.extraKilobytes) KB extra."
            rows.append(("oversized", "Image decodes at \(decoded) but draws at \(shown). \(extra)"))
        }
        if showsZeroSize(view) {
            let width = ViewSheetFormat.point(view.bounds.width)
            let height = ViewSheetFormat.point(view.bounds.height)
            rows.append(("zero", "Bounds are \(width)×\(height)."))
        }
        return rows.map { kind, message in
            .object(["kind": .string(kind), "message": .string(message)])
        }
    }

    @MainActor
    private static func issueCodes(for view: UIView) -> [String] {
        var codes: [String] = []
        if overflowMessage(of: view) != nil { codes.append("overflow") }
        if let image = imageLoad(of: view), image.extraKilobytes >= 128 { codes.append("oversized") }
        if showsZeroSize(view) { codes.append("zero") }
        return codes
    }

    @MainActor
    private static func showsZeroSize(_ view: UIView) -> Bool {
        guard !view.isHidden, view.alpha > 0.01 else { return false }
        guard view.bounds.width < 1 || view.bounds.height < 1 else { return false }
        return view.isAccessibilityElement || view is UIControl || view is UILabel || view is UIImageView
    }

    @MainActor
    private static func overflowMessage(of view: UIView) -> String? {
        if view is UIScrollView { return nil }
        var worstName = ""
        var worstEdge = ""
        var worstAmount: CGFloat = 1
        for child in view.subviews where !child.isHidden && child.alpha > 0.01 {
            let past = overflowPast(child: child, parent: view)
            if past.amount > worstAmount {
                worstAmount = past.amount
                worstEdge = past.edge
                worstName = String(String(describing: type(of: child)).prefix(40))
            }
        }
        guard worstAmount > 1 else { return nil }
        let amount = ViewSheetFormat.point(worstAmount)
        return "\(worstName) extends \(amount) pt past the \(worstEdge)."
    }

    private static func overflowPast(child: UIView, parent: UIView) -> (edge: String, amount: CGFloat) {
        let frame = child.frame
        let bounds = parent.bounds
        let edges = [
            ("top", bounds.minY - frame.minY),
            ("left", bounds.minX - frame.minX),
            ("bottom", frame.maxY - bounds.maxY),
            ("right", frame.maxX - bounds.maxX)
        ]
        return edges.max(by: { $0.1 < $1.1 }) ?? ("", 0)
    }

    @MainActor
    static func imageLoad(of view: UIView) -> ImageLoad? {
        guard let imageView = view as? UIImageView, let image = imageView.image, !image.isSymbolImage else {
            return nil
        }
        let scale = max(imageView.traitCollection.displayScale, 1)
        let displayWidth = Int(safe: (imageView.bounds.width * scale).rounded())
        let displayHeight = Int(safe: (imageView.bounds.height * scale).rounded())
        guard displayWidth > 0, displayHeight > 0 else { return nil }
        let pixelWidth = max(0, Int(safe: (image.size.width * image.scale).rounded()))
        let pixelHeight = max(0, Int(safe: (image.size.height * image.scale).rounded()))
        // Each side is capped at Int32.max, so these products fit in Int.
        let extraPixels = max(0, pixelWidth * pixelHeight - displayWidth * displayHeight)
        let extraKilobytes = Int(safe: (Double(extraPixels) * 4 / 1024).rounded())
        return ImageLoad(
            pixelWidth: pixelWidth,
            pixelHeight: pixelHeight,
            displayWidth: displayWidth,
            displayHeight: displayHeight,
            extraKilobytes: extraKilobytes
        )
    }

    @MainActor
    private static func baselineOffset(of view: UIView) -> CGFloat? {
        guard let label = view as? UILabel else { return nil }
        let rect = label.textRect(forBounds: label.bounds, limitedToNumberOfLines: label.numberOfLines)
        return rect.minY + label.font.ascender
    }

    /// Layout margins worth drawing on every view. The stock 8 pt margin is not padding.
    @MainActor
    static func notablePadding(of view: UIView) -> [CGFloat]? {
        let padding = physicalPadding(of: view)
        guard padding.contains(where: { $0 > 0.5 }) else { return nil }
        if let stack = view as? UIStackView, stack.isLayoutMarginsRelativeArrangement {
            return padding
        }
        let stock = padding.allSatisfy { abs($0 - 8) < 0.5 }
        return stock ? nil : padding
    }

    @MainActor
    private static func physicalPadding(of view: UIView) -> [CGFloat] {
        let margins = view.directionalLayoutMargins
        let rightToLeft = view.effectiveUserInterfaceLayoutDirection == .rightToLeft
        let left = rightToLeft ? margins.trailing : margins.leading
        let right = rightToLeft ? margins.leading : margins.trailing
        return [margins.top, left, margins.bottom, right]
    }

    @MainActor
    private static func margin(of view: UIView) -> [CGFloat] {
        guard let parent = view.superview else { return [0, 0, 0, 0] }
        let frame = view.frame
        let bounds = parent.bounds
        return [
            frame.minY - bounds.minY,
            frame.minX - bounds.minX,
            bounds.maxY - frame.maxY,
            bounds.maxX - frame.maxX
        ]
    }

    private static func numbers(_ values: [CGFloat]) -> JSONValue {
        .array(values.map { .number(Double($0)) })
    }
}
#endif
#endif
