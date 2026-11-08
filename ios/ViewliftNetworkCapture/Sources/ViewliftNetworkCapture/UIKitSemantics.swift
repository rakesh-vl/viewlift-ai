#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// What VoiceOver would say about a stock UIKit view when the accessibility runtime is not loaded.
/// Without VoiceOver or a UI-test host, UIKit controls report `isAccessibilityElement == false`
/// and no label, so the tree falls back to these class-based descriptions.
struct UIKitSemantics {
    var label: String
    var value = ""
    var traits: Set<String> = []
    /// False for cells: the cell is listed, and its buttons and labels are listed too.
    var isLeaf = true

    @MainActor
    static func describe(_ view: UIView) -> UIKitSemantics? {
        let explicit = view.accessibilityLabel ?? ""
        switch view {
        case let field as UITextField:
            let label = explicit.isEmpty ? (field.placeholder ?? "") : explicit
            return UIKitSemantics(label: label, value: field.isSecureTextEntry ? "" : (field.text ?? ""))
        case let textView as UITextView:
            return UIKitSemantics(label: explicit, value: String((textView.text ?? "").prefix(200)))
        case let label as UILabel:
            let text = explicit.isEmpty ? (label.text ?? "") : explicit
            return text.isEmpty ? nil : UIKitSemantics(label: text, traits: ["statictext"])
        case is UIImageView:
            return explicit.isEmpty ? nil : UIKitSemantics(label: explicit, traits: ["image"])
        case is UITableViewCell, is UICollectionViewCell:
            return UIKitSemantics(label: explicit.isEmpty ? spokenText(in: view) : explicit, isLeaf: false)
        case let control as UIControl:
            return describeControl(control, explicit: explicit)
        default:
            return nil
        }
    }

    @MainActor
    private static func describeControl(_ control: UIControl, explicit: String) -> UIKitSemantics {
        #if os(iOS)
        if let toggle = control as? UISwitch {
            return UIKitSemantics(label: explicit, value: toggle.isOn ? "1" : "0")
        }
        if let slider = control as? UISlider {
            return UIKitSemantics(label: explicit, value: String(format: "%.2f", slider.value), traits: ["adjustable"])
        }
        #endif
        if let segments = control as? UISegmentedControl {
            let index = segments.selectedSegmentIndex
            let title = index >= 0 ? (segments.titleForSegment(at: index) ?? "") : ""
            return UIKitSemantics(label: explicit, value: title)
        }
        let title = (control as? UIButton)?.currentTitle ?? ""
        let label = explicit.isEmpty ? (title.isEmpty ? spokenText(in: control) : title) : explicit
        return UIKitSemantics(label: label, traits: ["button"])
    }

    /// Visible label text inside a view, joined the way VoiceOver reads a cell or bar button.
    @MainActor
    private static func spokenText(in view: UIView) -> String {
        var parts: [String] = []
        collect(view, into: &parts)
        return String(parts.joined(separator: ", ").prefix(200))
    }

    @MainActor
    private static func collect(_ view: UIView, into parts: inout [String]) {
        guard parts.count < 3, !view.isHidden, view.alpha > 0.01 else { return }
        if let label = view as? UILabel, let text = label.text, !text.isEmpty {
            parts.append(text)
            return
        }
        for child in view.subviews {
            collect(child, into: &parts)
        }
    }
}
#endif
#endif
