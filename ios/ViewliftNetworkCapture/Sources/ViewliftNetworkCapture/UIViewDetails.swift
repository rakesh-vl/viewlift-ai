#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Layout, content, accessibility, and constraint rows for one view's property sheet.
enum UIViewDetails {
    @MainActor
    static func sections(for view: UIView) -> [ViewSheetSection] {
        [
            layout(view), appearance(view), content(view),
            accessibility(view), placement(view), constraints(view)
        ]
    }

    @MainActor
    private static func layout(_ view: UIView) -> ViewSheetSection {
        let frame = view.convert(view.bounds, to: nil)
        let origin = "x \(ViewSheetFormat.point(frame.origin.x)), y \(ViewSheetFormat.point(frame.origin.y))"
        let size = "\(ViewSheetFormat.point(frame.width)) × \(ViewSheetFormat.point(frame.height))"
        var rows = [
            ("Frame", "\(origin), \(size)"),
            ("Bounds", size),
            ("Content mode", ViewSheetFormat.contentMode(view.contentMode)),
            ("Hugging", axisPriorities(view, hugging: true)),
            ("Compression", axisPriorities(view, hugging: false))
        ]
        let insets = view.safeAreaInsets
        if insets != .zero {
            rows.append(("Safe area", edge(insets)))
        }
        if view.transform != .identity {
            rows.append(("Transform", transform(view.transform)))
        }
        let autoresizing = view.translatesAutoresizingMaskIntoConstraints ? "Yes" : "No"
        rows.append(("Autoresizing constraints", autoresizing))
        return ViewSheetSection(title: "Layout", rows: rows)
    }

    @MainActor
    private static func appearance(_ view: UIView) -> ViewSheetSection {
        let percent = Int((max(0, min(1, view.alpha)) * 100).rounded())
        var rows = [
            ("Shown", view.isHidden ? "No" : "Yes"),
            ("Opacity", "\(percent)%"),
            ("Touches", view.isUserInteractionEnabled ? "Enabled" : "Ignored"),
            ("Clips content", view.clipsToBounds ? "Yes" : "No"),
            ("Opaque", view.isOpaque ? "Yes" : "No")
        ]
        if let background = ViewSheetFormat.colorHex(view.backgroundColor, traits: view.traitCollection) {
            rows.append(("Background", background))
        }
        if let tint = ViewSheetFormat.colorHex(view.tintColor, traits: view.traitCollection) {
            rows.append(("Tint", tint))
        }
        if view.layer.cornerRadius > 0 {
            rows.append(("Corner radius", ViewSheetFormat.point(view.layer.cornerRadius)))
        }
        if view.layer.borderWidth > 0 {
            rows.append(("Border", border(view)))
        }
        if view.layer.shadowOpacity > 0 {
            let opacity = ViewSheetFormat.point(CGFloat(view.layer.shadowOpacity))
            let radius = ViewSheetFormat.point(view.layer.shadowRadius)
            rows.append(("Shadow", "opacity \(opacity), radius \(radius)"))
        }
        return ViewSheetSection(title: "Appearance", rows: rows)
    }

    @MainActor
    private static func content(_ view: UIView) -> ViewSheetSection {
        ViewSheetSection(title: "Content", rows: UIViewContent.rows(for: view))
    }

    @MainActor
    private static func accessibility(_ view: UIView) -> ViewSheetSection {
        var rows: [(String, String)] = [
            ("Accessibility element", view.isAccessibilityElement ? "Yes" : "No"),
            ("Traits", ViewSheetFormat.traits(view.accessibilityTraits))
        ]
        if let label = view.accessibilityLabel, !label.isEmpty {
            rows.append(("Label", ViewSheetFormat.clip(label)))
        }
        if let hint = view.accessibilityHint, !hint.isEmpty {
            rows.append(("Hint", ViewSheetFormat.clip(hint)))
        }
        if let value = view.accessibilityValue, !value.isEmpty {
            rows.append(("Value", ViewSheetFormat.clip(value)))
        }
        if let identifier = view.accessibilityIdentifier, !identifier.isEmpty {
            rows.append(("Identifier", ViewSheetFormat.clip(identifier)))
        }
        return ViewSheetSection(title: "Accessibility", rows: rows)
    }

    @MainActor
    private static func placement(_ view: UIView) -> ViewSheetSection {
        var rows: [(String, String)] = []
        if let superview = view.superview {
            rows.append(("Inside", String(String(describing: type(of: superview)).prefix(80))))
        }
        let owners = UIViewSheet.controllers(from: view).map { controller in
            UITypeName.moduleAndShort(
                qualified: String(reflecting: type(of: controller)),
                described: String(describing: type(of: controller))
            ).short
        }
        if owners.count > 1 {
            rows.append(("Controller chain", ViewSheetFormat.clip(owners.joined(separator: " → "))))
        }
        if !view.subviews.isEmpty {
            rows.append(("Subviews", subviewSummary(view)))
        }
        let gestures = (view.gestureRecognizers ?? []).prefix(6).map { String(describing: type(of: $0)) }
        if !gestures.isEmpty {
            rows.append(("Gestures", gestures.joined(separator: ", ")))
        }
        return ViewSheetSection(title: "Where it sits", rows: rows)
    }

    @MainActor
    private static func constraints(_ view: UIView) -> ViewSheetSection {
        var items = Array(view.constraints.prefix(12))
        if let superview = view.superview {
            let extra = superview.constraints.filter { constraint in
                let first = (constraint.firstItem as AnyObject?) === view
                let second = (constraint.secondItem as AnyObject?) === view
                return first || second
            }
            items.append(contentsOf: extra.prefix(max(0, 12 - items.count)))
        }
        var rows = items.map { constraint in
            UILayoutSheet.readable(constraint, owner: view)
        }
        if view.constraints.count > items.count {
            rows.append(("More", "\(view.constraints.count - items.count) more on this view"))
        }
        return ViewSheetSection(title: "Constraints", rows: rows)
    }

    @MainActor
    private static func axisPriorities(_ view: UIView, hugging: Bool) -> String {
        let horizontal = hugging
            ? view.contentHuggingPriority(for: .horizontal).rawValue
            : view.contentCompressionResistancePriority(for: .horizontal).rawValue
        let vertical = hugging
            ? view.contentHuggingPriority(for: .vertical).rawValue
            : view.contentCompressionResistancePriority(for: .vertical).rawValue
        let across = ViewSheetFormat.point(CGFloat(horizontal))
        let down = ViewSheetFormat.point(CGFloat(vertical))
        return "H \(across) · V \(down)"
    }

    private static func edge(_ insets: UIEdgeInsets) -> String {
        let top = ViewSheetFormat.point(insets.top)
        let left = ViewSheetFormat.point(insets.left)
        let bottom = ViewSheetFormat.point(insets.bottom)
        let right = ViewSheetFormat.point(insets.right)
        return "top \(top), left \(left), bottom \(bottom), right \(right)"
    }

    private static func transform(_ value: CGAffineTransform) -> String {
        let prefix = "a \(ViewSheetFormat.point(value.a)), b \(ViewSheetFormat.point(value.b))"
        let middle = "c \(ViewSheetFormat.point(value.c)), d \(ViewSheetFormat.point(value.d))"
        let shift = "tx \(ViewSheetFormat.point(value.tx)), ty \(ViewSheetFormat.point(value.ty))"
        return "\(prefix), \(middle), \(shift)"
    }

    @MainActor
    private static func border(_ view: UIView) -> String {
        let width = ViewSheetFormat.point(view.layer.borderWidth)
        let color = view.layer.borderColor.flatMap { cgColor in
            ViewSheetFormat.colorHex(UIColor(cgColor: cgColor), traits: view.traitCollection)
        }
        return "\(width) \(color ?? "")".trimmingCharacters(in: .whitespaces)
    }

    @MainActor
    private static func subviewSummary(_ view: UIView) -> String {
        let names = view.subviews.prefix(8).map { String(String(describing: type(of: $0)).prefix(40)) }
        let more = view.subviews.count > names.count ? ", …" : ""
        return "\(view.subviews.count) · \(names.joined(separator: ", "))\(more)"
    }
}
#endif
#endif
