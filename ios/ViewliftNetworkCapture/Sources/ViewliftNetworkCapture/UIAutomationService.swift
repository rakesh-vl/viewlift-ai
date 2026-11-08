#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Drives the app the way XCUITest would, without a test runner: activates controls and
/// accessibility elements, scrolls scroll views, and types through the focused text input.
@MainActor
enum UIAutomationService {
    static let maxTextLength = 2_000

    static func tap(_ payload: AutomationPayload) -> [String: JSONValue] {
        let (query, found) = locate(payload)
        guard let node = found else { return notFound(query) }
        guard node.info.enabled else {
            return failure("Element is disabled.", element: node)
        }
        guard let method = UIActivation.activate(node) else {
            return failure(
                "The SDK found no action on this element. On a simulator, tap without client_id.",
                element: node
            )
        }
        return ["tapped": .bool(true), "method": .string(method), "element": node.info.json()]
    }

    /// Long press has no public trigger. Context menus usually expose accessibility custom actions,
    /// so this runs the custom action named by `action`, or the only one present.
    static func longPress(_ payload: AutomationPayload) -> [String: JSONValue] {
        let (query, found) = locate(payload)
        guard let node = found else { return notFound(query) }
        let actions = node.object.accessibilityCustomActions ?? []
        let wanted = payload.string("action").lowercased()
        let chosen = wanted.isEmpty
            ? (actions.count == 1 ? actions.first : nil)
            : actions.first { $0.name.lowercased() == wanted }
        guard let chosen, UIActivation.perform(chosen) else {
            var result = failure(
                "No custom action to run. Pass action, or long-press on a simulator without client_id.",
                element: node
            )
            result["actions"] = .array(actions.map { .string($0.name) })
            return result
        }
        return ["performed": .string(chosen.name), "element": node.info.json()]
    }

    /// `ui.swipe` takes finger direction. `ui.scroll` takes content direction.
    static func scroll(_ payload: AutomationPayload, fingerDirection: Bool) -> [String: JSONValue] {
        guard let raw = SwipeDirection(rawValue: payload.string("direction").lowercased()) else {
            return ["ok": .bool(false), "detail": .string("direction must be up, down, left, or right")]
        }
        let direction = fingerDirection ? raw.scrollDirection : raw
        let query = ElementQuery(payload: payload)
        let anchor = query.isEmpty ? nil : AccessibilityTreeService.resolve(query)?.view
        guard let scrollView = ScrollFinder.scrollView(near: anchor, direction: direction) else {
            return ["ok": .bool(false), "scrolled": .bool(false), "detail": .string("No scroll view moves that way.")]
        }
        let metrics = ScrollFinder.metrics(of: scrollView)
        let target = metrics.target(direction, fraction: payload.number("fraction") ?? 0.8)
        scrollView.setContentOffset(target, animated: false)
        scrollView.delegate?.scrollViewDidEndDecelerating?(scrollView)
        return [
            "scrolled": .bool(true),
            "direction": .string(direction.rawValue),
            "offset": .object(["x": .number(Double(target.x)), "y": .number(Double(target.y))]),
            "at_end": .bool(!ScrollFinder.metrics(of: scrollView).canScroll(direction)),
            "view_id": .string(String(ObjectIdentifier(scrollView).hashValue))
        ]
    }

    static func typeText(_ payload: AutomationPayload) -> [String: JSONValue] {
        let text = payload.text("text")
        guard text.count <= maxTextLength else {
            return ["ok": .bool(false), "detail": .string("text is longer than \(maxTextLength) characters")]
        }
        let query = ElementQuery(payload: payload)
        if !query.isEmpty {
            guard let node = AccessibilityTreeService.resolve(query) else { return notFound(query) }
            guard let field = TextInputFinder.input(in: node.view) else {
                return failure("Element is not a text input.", element: node)
            }
            field.becomeFirstResponder()
        }
        guard let input = TextInputFinder.firstResponder() else {
            return ["ok": .bool(false), "detail": .string("No focused text input. Pass identifier or tap a field.")]
        }
        if payload.bool("replace") {
            TextInputFinder.clear(input)
        }
        input.insertText(text)
        let submitted = payload.bool("submit") && TextInputFinder.submit(input)
        return ["typed": .number(Double(text.count)), "submitted": .bool(submitted)]
    }

    static func key(_ payload: AutomationPayload) -> [String: JSONValue] {
        let key = payload.string("key").lowercased()
        let input = TextInputFinder.firstResponder()
        switch key {
        case "return", "enter":
            guard let input else { return noInput(key) }
            return ["key": .string(key), "submitted": .bool(TextInputFinder.submit(input))]
        case "delete", "backspace":
            guard let input else { return noInput(key) }
            let count = payload.int("count", fallback: 1, range: 1...200)
            for _ in 0..<count { input.deleteBackward() }
            return ["key": .string(key), "deleted": .number(Double(count))]
        case "tab":
            return ["key": .string(key), "moved": .bool(TextInputFinder.focusNext(after: input))]
        case "escape":
            let resigned = input?.resignFirstResponder() ?? false
            return ["key": .string(key), "dismissed": .bool(resigned)]
        default:
            return ["ok": .bool(false), "detail": .string("key must be return, delete, tab, or escape")]
        }
    }

    /// The element named by the query, or the smallest element under `x`/`y` when no query is given.
    private static func locate(_ payload: AutomationPayload) -> (ElementQuery, AccessibilityNode?) {
        let query = ElementQuery(payload: payload)
        guard query.isEmpty else { return (query, AccessibilityTreeService.resolve(query)) }
        guard let pointX = payload.number("x"), let pointY = payload.number("y") else { return (query, nil) }
        return (query, AccessibilityTreeService.node(at: CGPoint(x: pointX, y: pointY)))
    }

    private static func notFound(_ query: ElementQuery) -> [String: JSONValue] {
        ["ok": .bool(false), "found": .bool(false), "detail": .string("No element matched."), "query": query.json()]
    }

    private static func noInput(_ key: String) -> [String: JSONValue] {
        ["ok": .bool(false), "key": .string(key), "detail": .string("No focused text input.")]
    }

    private static func failure(_ detail: String, element: AccessibilityNode) -> [String: JSONValue] {
        ["ok": .bool(false), "detail": .string(detail), "element": element.info.json()]
    }
}

/// Runs the action a finger tap or remote select would run on an element.
@MainActor
enum UIActivation {
    /// Returns how the element was activated, or nil when nothing could be triggered.
    static func activate(_ node: AccessibilityNode) -> String? {
        if node.isView, let method = activateView(node.view) {
            return method
        }
        if node.object.accessibilityActivate() {
            return "accessibility_activate"
        }
        if !node.isView, let method = activateView(node.view) {
            return method
        }
        return nil
    }

    static func activateView(_ view: UIView) -> String? {
        if let input = TextInputFinder.input(in: view) {
            return input.becomeFirstResponder() ? "focus" : nil
        }
        if let control = ancestor(of: view, as: UIControl.self) {
            return trigger(control)
        }
        if let method = selectCell(containing: view) {
            return method
        }
        return nil
    }

    static func perform(_ action: UIAccessibilityCustomAction) -> Bool {
        if let handler = action.actionHandler {
            return handler(action)
        }
        guard let target = action.target as? NSObject, target.responds(to: action.selector) else {
            return false
        }
        _ = target.perform(action.selector, with: action)
        return true
    }

    private static func trigger(_ control: UIControl) -> String {
        #if os(iOS)
        if let toggle = control as? UISwitch {
            toggle.setOn(!toggle.isOn, animated: true)
            toggle.sendActions(for: .valueChanged)
            return "toggle"
        }
        #endif
        if control.allControlEvents.contains(.primaryActionTriggered) {
            control.sendActions(for: .primaryActionTriggered)
            return "primary_action"
        }
        control.sendActions(for: .touchUpInside)
        return "touch_up_inside"
    }

    private static func selectCell(containing view: UIView) -> String? {
        if let cell = ancestor(of: view, as: UITableViewCell.self),
           let table = ancestor(of: cell, as: UITableView.self),
           let path = table.indexPath(for: cell) {
            let target = table.delegate?.tableView?(table, willSelectRowAt: path) ?? path
            table.selectRow(at: target, animated: false, scrollPosition: .none)
            table.delegate?.tableView?(table, didSelectRowAt: target)
            return "select_row"
        }
        if let cell = ancestor(of: view, as: UICollectionViewCell.self),
           let collection = ancestor(of: cell, as: UICollectionView.self),
           let path = collection.indexPath(for: cell) {
            guard collection.delegate?.collectionView?(collection, shouldSelectItemAt: path) ?? true else {
                return nil
            }
            collection.selectItem(at: path, animated: false, scrollPosition: [])
            collection.delegate?.collectionView?(collection, didSelectItemAt: path)
            return "select_item"
        }
        return nil
    }

    static func ancestor<T: UIView>(of view: UIView, as type: T.Type, levels: Int = 6) -> T? {
        var current: UIView? = view
        var remaining = levels
        while let candidate = current, remaining >= 0 {
            if let match = candidate as? T { return match }
            current = candidate.superview
            remaining -= 1
        }
        return nil
    }
}

/// Text field lookup, clearing, and submit for `ui.type_text` and `ui.key`.
@MainActor
enum TextInputFinder {
    typealias Input = UIKeyInput & UIResponder

    static func input(in view: UIView) -> Input? {
        if let field = view as? UITextField { return field }
        if let textView = view as? UITextView, isEditable(textView) { return textView }
        #if os(iOS)
        if let searchBar = view as? UISearchBar { return searchBar.searchTextField }
        #endif
        for child in view.subviews {
            if let match = input(in: child) { return match }
        }
        return nil
    }

    /// tvOS text views are read-only.
    static func isEditable(_ textView: UITextView) -> Bool {
        #if os(iOS)
        return textView.isEditable
        #else
        return false
        #endif
    }

    static func firstResponder() -> Input? {
        for window in AutomationScreen.windows() {
            if let match = firstResponder(in: window) { return match }
        }
        return nil
    }

    static func clear(_ input: Input) {
        guard let text = input as? UITextInput,
              let range = text.textRange(from: text.beginningOfDocument, to: text.endOfDocument) else {
            return
        }
        text.replace(range, withText: "")
    }

    /// Return key: asks the delegate like the keyboard does, then fires the end-on-exit event.
    static func submit(_ input: Input) -> Bool {
        if let field = input as? UITextField {
            let allowed = field.delegate?.textFieldShouldReturn?(field) ?? true
            if allowed { field.sendActions(for: .editingDidEndOnExit) }
            return allowed
        }
        input.insertText("\n")
        return true
    }

    /// Moves to the next text input in reading order, wrapping to the first.
    static func focusNext(after current: Input?) -> Bool {
        guard let window = AutomationScreen.keyWindow() else { return false }
        var inputs: [UIView] = []
        collect(in: window, into: &inputs)
        let ordered = inputs.sorted { lhs, rhs in
            let left = lhs.convert(lhs.bounds, to: nil)
            let right = rhs.convert(rhs.bounds, to: nil)
            return abs(left.minY - right.minY) > 4 ? left.minY < right.minY : left.minX < right.minX
        }
        guard !ordered.isEmpty else { return false }
        let position = ordered.firstIndex { $0 === current } ?? -1
        return ordered[(position + 1) % ordered.count].becomeFirstResponder()
    }

    private static func firstResponder(in view: UIView) -> Input? {
        if view.isFirstResponder, let input = view as? Input { return input }
        for child in view.subviews {
            if let match = firstResponder(in: child) { return match }
        }
        return nil
    }

    private static func collect(in view: UIView, into inputs: inout [UIView]) {
        guard !view.isHidden, view.alpha > 0.01 else { return }
        if view is UITextField || (view as? UITextView).map(isEditable) == true {
            inputs.append(view)
            return
        }
        for child in view.subviews {
            collect(in: child, into: &inputs)
        }
    }
}

/// Picks the scroll view a swipe should move.
@MainActor
enum ScrollFinder {
    static func scrollView(near anchor: UIView?, direction: SwipeDirection) -> UIScrollView? {
        if let anchor {
            var current: UIView? = anchor
            while let view = current {
                if let scroll = view as? UIScrollView, usable(scroll, direction) { return scroll }
                current = view.superview
            }
        }
        guard let window = AutomationScreen.keyWindow() else { return nil }
        var found: [UIScrollView] = []
        collect(in: window, into: &found)
        return found
            .filter { usable($0, direction) }
            .max { visibleArea($0, in: window) < visibleArea($1, in: window) }
    }

    static func metrics(of scroll: UIScrollView) -> ScrollMetrics {
        let inset = scroll.adjustedContentInset
        return ScrollMetrics(
            offset: scroll.contentOffset,
            contentSize: scroll.contentSize,
            viewport: scroll.bounds.size,
            insets: ScrollInsets(top: inset.top, left: inset.left, bottom: inset.bottom, right: inset.right)
        )
    }

    private static func usable(_ scroll: UIScrollView, _ direction: SwipeDirection) -> Bool {
        scroll.isScrollEnabled && !scroll.isHidden && scroll.window != nil && metrics(of: scroll).canScroll(direction)
    }

    private static func visibleArea(_ view: UIView, in window: UIWindow) -> CGFloat {
        let frame = view.convert(view.bounds, to: window).intersection(window.bounds)
        return frame.isNull ? 0 : frame.width * frame.height
    }

    private static func collect(in view: UIView, into found: inout [UIScrollView]) {
        guard !view.isHidden, view.alpha > 0.01, !UILayoutDebugService.isOverlay(view) else { return }
        if let scroll = view as? UIScrollView {
            found.append(scroll)
        }
        for child in view.subviews {
            collect(in: child, into: &found)
        }
    }
}
#endif
#endif
