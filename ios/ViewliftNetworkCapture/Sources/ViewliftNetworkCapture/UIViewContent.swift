#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Text, image, scroll, and control values for one view.
enum UIViewContent {
    private struct TextFacts {
        let text: String?
        let font: UIFont?
        let color: UIColor?
        let alignment: NSTextAlignment?
        let lines: Int?
        let traits: UITraitCollection
    }

    @MainActor
    static func rows(for view: UIView) -> [(String, String)] {
        textRows(view) + imageRows(view) + scrollRows(view) + containerRows(view) + controlRows(view)
    }

    @MainActor
    private static func textRows(_ view: UIView) -> [(String, String)] {
        switch view {
        case let label as UILabel:
            return textRows(TextFacts(
                text: label.text, font: label.font, color: label.textColor,
                alignment: label.textAlignment, lines: label.numberOfLines, traits: label.traitCollection
            ))
        case let button as UIButton:
            return buttonRows(button)
        case let field as UITextField:
            return fieldRows(field)
        case let textView as UITextView:
            return textRows(TextFacts(
                text: textView.text, font: textView.font, color: textView.textColor,
                alignment: textView.textAlignment, lines: 0, traits: textView.traitCollection
            ))
        default:
            return []
        }
    }

    @MainActor
    private static func buttonRows(_ button: UIButton) -> [(String, String)] {
        var rows = textRows(TextFacts(
            text: button.currentTitle ?? button.configuration?.title,
            font: button.titleLabel?.font,
            color: button.titleColor(for: .normal),
            alignment: nil,
            lines: button.titleLabel?.numberOfLines,
            traits: button.traitCollection
        ))
        if let subtitle = button.configuration?.subtitle, !subtitle.isEmpty {
            rows.append(("Subtitle", ViewSheetFormat.clip(subtitle)))
        }
        rows.append(("Enabled", button.isEnabled ? "Yes" : "No"))
        return rows
    }

    @MainActor
    private static func fieldRows(_ field: UITextField) -> [(String, String)] {
        var rows = textRows(TextFacts(
            text: field.text, font: field.font, color: field.textColor,
            alignment: field.textAlignment, lines: 1, traits: field.traitCollection
        ))
        if let placeholder = field.placeholder, !placeholder.isEmpty {
            rows.append(("Placeholder", ViewSheetFormat.clip(placeholder)))
        }
        return rows
    }

    private static func textRows(_ facts: TextFacts) -> [(String, String)] {
        var rows: [(String, String)] = []
        if let text = facts.text, !text.isEmpty { rows.append(("Text", ViewSheetFormat.clip(text))) }
        if let font = facts.font {
            rows.append(("Font", "\(font.fontName) \(ViewSheetFormat.point(font.pointSize))"))
        }
        if let color = facts.color, let hex = ViewSheetFormat.colorHex(color, traits: facts.traits) {
            rows.append(("Text color", hex))
        }
        if let alignment = facts.alignment {
            rows.append(("Alignment", ViewSheetFormat.alignment(alignment)))
        }
        if let lines = facts.lines {
            rows.append(("Lines", lines == 0 ? "Unlimited" : String(lines)))
        }
        return rows
    }

    @MainActor
    private static func imageRows(_ view: UIView) -> [(String, String)] {
        guard let imageView = view as? UIImageView else { return [] }
        guard let image = imageView.image else { return [("Image", "None")] }
        let symbol = image.isSymbolImage ? " · SF Symbol" : ""
        let size = "\(Int(image.size.width))×\(Int(image.size.height)) pt"
        return [("Image", "\(size) @\(ViewSheetFormat.point(image.scale))x\(symbol)")]
    }

    @MainActor
    private static func scrollRows(_ view: UIView) -> [(String, String)] {
        guard let scroll = view as? UIScrollView else { return [] }
        let width = ViewSheetFormat.point(scroll.contentSize.width)
        let height = ViewSheetFormat.point(scroll.contentSize.height)
        let size = "\(width) × \(height)"
        let offsetX = ViewSheetFormat.point(scroll.contentOffset.x)
        let offsetY = ViewSheetFormat.point(scroll.contentOffset.y)
        let offset = "x \(offsetX), y \(offsetY)"
        return [
            ("Content size", size),
            ("Offset", offset),
            ("Zoom", ViewSheetFormat.point(scroll.zoomScale))
        ]
    }

    @MainActor
    private static func containerRows(_ view: UIView) -> [(String, String)] {
        var rows: [(String, String)] = []
        if let stack = view as? UIStackView {
            let axis = stack.axis == .vertical ? "Vertical" : "Horizontal"
            let spacing = ViewSheetFormat.point(stack.spacing)
            rows.append(("Stack", "\(axis), spacing \(spacing), \(stack.arrangedSubviews.count) arranged"))
        }
        if let table = view as? UITableView {
            rows.append(("Table", "\(table.numberOfSections) sections, \(table.visibleCells.count) visible rows"))
        }
        if let collection = view as? UICollectionView {
            let sections = collection.numberOfSections
            let visible = collection.visibleCells.count
            rows.append(("Collection", "\(sections) sections, \(visible) visible cells"))
        }
        return rows
    }

    @MainActor
    private static func controlRows(_ view: UIView) -> [(String, String)] {
        var rows: [(String, String)] = []
        if let control = view as? UIControl, !(view is UIButton) {
            rows.append(("Enabled", control.isEnabled ? "Yes" : "No"))
            if control.isSelected { rows.append(("Selected", "Yes")) }
        }
        if let indicator = view as? UIActivityIndicatorView {
            rows.append(("Spinning", indicator.isAnimating ? "Yes" : "No"))
        }
        rows += platformControls(view)
        if let progress = view as? UIProgressView {
            rows.append(("Progress", ViewSheetFormat.point(CGFloat(progress.progress))))
        }
        if let segments = view as? UISegmentedControl, segments.numberOfSegments > 0 {
            let choice = "\(segments.selectedSegmentIndex + 1) of \(segments.numberOfSegments)"
            rows.append(("Segments", choice))
        }
        return rows
    }

    @MainActor
    private static func platformControls(_ view: UIView) -> [(String, String)] {
        #if os(iOS)
        var rows: [(String, String)] = []
        if let toggle = view as? UISwitch { rows.append(("On", toggle.isOn ? "Yes" : "No")) }
        if let slider = view as? UISlider {
            rows.append(("Value", ViewSheetFormat.point(CGFloat(slider.value))))
        }
        if let stepper = view as? UIStepper {
            rows.append(("Value", ViewSheetFormat.point(CGFloat(stepper.value))))
        }
        if let pages = view as? UIPageControl {
            rows.append(("Pages", "\(pages.currentPage + 1) of \(pages.numberOfPages)"))
        }
        return rows
        #else
        _ = view
        return []
        #endif
    }

}
#endif
#endif
