#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Constraint equations and stack flex for the layout explorer.
enum UILayoutSheet {
    @MainActor
    static func constraints(for view: UIView) -> [JSONValue] {
        var items = Array(view.constraints.prefix(16))
        if let parent = view.superview, items.count < 16 {
            let extra = parent.constraints.filter { constraint in
                let first = (constraint.firstItem as AnyObject?) === view
                let second = (constraint.secondItem as AnyObject?) === view
                return first || second
            }
            items.append(contentsOf: extra.prefix(16 - items.count))
        }
        return items.map { constraint in
            constraintRow(constraint, owner: view)
        }
    }

    @MainActor
    static func flex(for view: UIView) -> [String: JSONValue]? {
        if let stack = view as? UIStackView {
            return flex(of: stack, selected: "")
        }
        guard let stack = view.superview as? UIStackView,
              stack.arrangedSubviews.contains(where: { $0 === view }) else {
            return nil
        }
        let selected = String(ObjectIdentifier(view).hashValue)
        return flex(of: stack, selected: selected)
    }

    @MainActor
    private static func flex(of stack: UIStackView, selected: String) -> [String: JSONValue] {
        let arranged = stack.arrangedSubviews
        let shown = arranged.prefix(12).map { child in childRow(child) }
        return [
            "axis": .string(stack.axis == .vertical ? "vertical" : "horizontal"),
            "alignment": .string(alignmentName(stack.alignment)),
            "distribution": .string(distributionName(stack.distribution)),
            "spacing": .string(ViewSheetFormat.point(stack.spacing)),
            "selected": .string(selected),
            "truncated": .bool(arranged.count > shown.count),
            "children": .array(shown)
        ]
    }

    @MainActor
    private static func childRow(_ child: UIView) -> JSONValue {
        let hugging = child.contentHuggingPriority(for: .horizontal).rawValue
        let name = String(String(describing: type(of: child)).prefix(48))
        return .object([
            "id": .string(String(ObjectIdentifier(child).hashValue)),
            "class": .string(name),
            "width": .number(Double(child.bounds.width)),
            "height": .number(Double(child.bounds.height)),
            "hidden": .bool(child.isHidden),
            "hugging": .number(Double(hugging))
        ])
    }

    private static func constraintRow(_ constraint: NSLayoutConstraint, owner: UIView) -> JSONValue {
        let facts = facts(for: constraint, owner: owner)
        var row: [String: JSONValue] = [
            "title": .string(ConstraintCopy.title(facts)),
            "summary": .string(ConstraintCopy.summary(facts)),
            "equation": .string(equation(constraint, owner: owner)),
            "priority": .number(Double(constraint.priority.rawValue)),
            "active": .bool(constraint.isActive)
        ]
        let note = ConstraintCopy.note(facts)
        if !note.isEmpty { row["note"] = .string(note) }
        if let name = constraint.identifier, !name.isEmpty {
            row["identifier"] = .string(String(name.prefix(80)))
        }
        return .object(row)
    }

    static func readable(_ constraint: NSLayoutConstraint, owner: UIView) -> (title: String, text: String) {
        let facts = facts(for: constraint, owner: owner)
        let text = [ConstraintCopy.summary(facts), ConstraintCopy.note(facts)]
            .filter { !$0.isEmpty }
            .joined(separator: ". ")
        return (ConstraintCopy.title(facts), text)
    }

    private static func facts(for constraint: NSLayoutConstraint, owner: UIView) -> ConstraintFacts {
        let contentSize = String(describing: type(of: constraint)).contains("ContentSize")
        let axis: NSLayoutConstraint.Axis = constraint.firstAttribute == .height ? .vertical : .horizontal
        let hugging = Int(owner.contentHuggingPriority(for: axis).rawValue.rounded())
        let compression = Int(owner.contentCompressionResistancePriority(for: axis).rawValue.rounded())
        let hasOther = constraint.secondItem != nil && constraint.secondAttribute != .notAnAttribute
        return ConstraintFacts(
            attribute: spoken(constraint.firstAttribute),
            relation: relationKey(constraint.relation),
            constant: Double(constraint.constant),
            otherName: hasOther ? itemSpoken(constraint.secondItem, owner: owner) : "",
            otherAttribute: hasOther ? spoken(constraint.secondAttribute) : "",
            hasOther: hasOther,
            multiplier: Double(constraint.multiplier),
            contentSize: contentSize,
            hugging: hugging,
            compression: compression,
            priority: Int(constraint.priority.rawValue.rounded()),
            active: constraint.isActive
        )
    }

    private static func equation(_ constraint: NSLayoutConstraint, owner: UIView) -> String {
        let attribute = attributeName(constraint.firstAttribute)
        let left = "\(itemName(constraint.firstItem, owner: owner)).\(attribute)"
        let relation = relationName(constraint.relation)
        guard constraint.secondItem != nil, constraint.secondAttribute != .notAnAttribute else {
            return "\(left) \(relation) \(ViewSheetFormat.point(constraint.constant))"
        }
        let secondAttribute = attributeName(constraint.secondAttribute)
        let right = "\(itemName(constraint.secondItem, owner: owner)).\(secondAttribute)"
        if constraint.multiplier == 1 && constraint.constant == 0 {
            return "\(left) \(relation) \(right)"
        }
        if constraint.multiplier == 1 {
            let sign = constraint.constant > 0 ? "+" : "−"
            let magnitude = ViewSheetFormat.point(abs(constraint.constant))
            return "\(left) \(relation) \(right) \(sign) \(magnitude)"
        }
        let multiplier = ViewSheetFormat.point(CGFloat(constraint.multiplier))
        let constant = ViewSheetFormat.point(constraint.constant)
        return "\(left) \(relation) \(right) × \(multiplier) + \(constant)"
    }

    private static func spoken(_ attribute: NSLayoutConstraint.Attribute) -> String {
        let names: [NSLayoutConstraint.Attribute: String] = [
            .left: "Left", .right: "Right", .top: "Top", .bottom: "Bottom",
            .leading: "Leading", .trailing: "Trailing", .width: "Width", .height: "Height",
            .centerX: "Center X", .centerY: "Center Y", .lastBaseline: "Baseline",
            .firstBaseline: "First baseline"
        ]
        return names[attribute] ?? "Edge"
    }

    private static func relationKey(_ relation: NSLayoutConstraint.Relation) -> String {
        switch relation {
        case .greaterThanOrEqual: return "min"
        case .lessThanOrEqual: return "max"
        default: return "equal"
        }
    }

    private static func itemSpoken(_ item: AnyObject?, owner: UIView) -> String {
        guard let item else { return "nothing" }
        if item === (owner as AnyObject) { return "this view" }
        if let view = item as? UIView {
            if view === owner.superview { return "the parent" }
            let name = String(describing: type(of: view))
            return "the \(name)"
        }
        let guide = String(describing: type(of: item))
        if guide.contains("SafeArea") { return "the safe area" }
        if guide.contains("LayoutMargins") { return "the margins" }
        return "the layout guide"
    }

    private static func itemName(_ item: AnyObject?, owner: UIView) -> String {
        guard let item else { return "none" }
        if item === owner { return "self" }
        if let view = item as? UIView {
            if view === owner.superview { return "parent" }
            return String(String(describing: type(of: view)).prefix(40))
        }
        return "guide"
    }

    private static func attributeName(_ attribute: NSLayoutConstraint.Attribute) -> String {
        let names: [NSLayoutConstraint.Attribute: String] = [
            .left: "left", .right: "right", .top: "top", .bottom: "bottom",
            .leading: "leading", .trailing: "trailing", .width: "width", .height: "height",
            .centerX: "centerX", .centerY: "centerY", .lastBaseline: "baseline",
            .firstBaseline: "firstBaseline", .notAnAttribute: "none"
        ]
        return names[attribute] ?? "other"
    }

    private static func relationName(_ relation: NSLayoutConstraint.Relation) -> String {
        switch relation {
        case .equal: return "="
        case .greaterThanOrEqual: return "≥"
        case .lessThanOrEqual: return "≤"
        @unknown default: return "?"
        }
    }

    private static func alignmentName(_ alignment: UIStackView.Alignment) -> String {
        switch alignment {
        case .fill: return "fill"
        case .leading: return "leading"
        case .top: return "top"
        case .firstBaseline: return "firstBaseline"
        case .center: return "center"
        case .trailing: return "trailing"
        case .bottom: return "bottom"
        case .lastBaseline: return "lastBaseline"
        @unknown default: return "other"
        }
    }

    private static func distributionName(_ distribution: UIStackView.Distribution) -> String {
        switch distribution {
        case .fill: return "fill"
        case .fillEqually: return "fillEqually"
        case .fillProportionally: return "fillProportionally"
        case .equalSpacing: return "equalSpacing"
        case .equalCentering: return "equalCentering"
        @unknown default: return "other"
        }
    }
}
#endif
#endif
