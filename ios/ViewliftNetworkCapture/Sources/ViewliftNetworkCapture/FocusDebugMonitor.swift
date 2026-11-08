#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import ObjectiveC.runtime
import UIKit
#endif

/// Focused view and tvOS remote presses. View ids match `ui.hierarchy` and `ui.highlight`.
final class FocusDebugMonitor: @unchecked Sendable {
    static let shared = FocusDebugMonitor()

    private let lock = NSLock()
    private var enabled = false
    private var installed = false
    private var rejection: [String: JSONValue]?
    private var presses: [[String: JSONValue]] = []
    private var outbox: [DebugEvent] = []

    private init() {}

    func install() {
        #if canImport(UIKit)
        if Thread.isMainThread {
            installOnMain()
        } else {
            DispatchQueue.main.async { self.installOnMain() }
        }
        #endif
    }

    func drainEvents() -> [DebugEvent] {
        lock.lock()
        let copy = outbox
        outbox = []
        lock.unlock()
        return copy
    }

    #if canImport(UIKit)
    @MainActor
    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let rejected = rejection
        let recent = presses
        lock.unlock()
        return [
            "focused": .object(Self.focusedItem()),
            "last_rejection": rejected.map(JSONValue.object) ?? .null,
            "presses": .array(recent.reversed().map(JSONValue.object))
        ]
    }

    func notePresses(_ event: UIEvent) {
        guard isEnabled, let presses = event as? UIPressesEvent else { return }
        for press in presses.allPresses where press.phase == .began {
            guard let name = Self.buttonName(press.type) else { continue }
            rememberPress(name)
        }
    }

    func noteRejection(_ view: UIView, context: UIFocusUpdateContext) {
        let row: [String: JSONValue] = [
            "id": .string(Self.viewId(view)),
            "class": .string(String(describing: type(of: view))),
            "heading": .string(Self.headingName(context.focusHeading)),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
        lock.lock()
        rejection = row
        lock.unlock()
        enqueue(module: "ui", kind: "focus", payload: row.merging(["rejected": .bool(true)]) { _, new in new })
    }

    func noteUpdate(_ context: UIFocusUpdateContext) {
        guard let view = context.nextFocusedItem as? UIView else { return }
        let row = Self.describe(view, heading: context.focusHeading)
        enqueue(module: "ui", kind: "focus", payload: row)
    }

    private func installOnMain() {
        lock.lock()
        let already = installed
        installed = true
        enabled = true
        lock.unlock()
        guard !already else { return }
        UIView.viewliftInstallFocusMonitor()
    }

    private var isEnabled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return enabled
    }

    private func rememberPress(_ name: String) {
        let row: [String: JSONValue] = [
            "button": .string(name),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
        lock.lock()
        presses.append(row)
        if presses.count > 40 {
            presses.removeFirst(presses.count - 40)
        }
        lock.unlock()
        enqueue(module: "ui", kind: "remote", payload: row)
    }

    private func enqueue(module: String, kind: String, payload: [String: JSONValue]) {
        lock.lock()
        outbox.append(DebugEvent(module: module, kind: kind, payload: payload))
        if outbox.count > 40 {
            outbox.removeFirst(outbox.count - 40)
        }
        lock.unlock()
    }

    @MainActor
    private static func focusedItem() -> [String: JSONValue] {
        let windows = AutomationScreen.allWindows()
        for window in windows {
            guard let item = UIFocusSystem.focusSystem(for: window)?.focusedItem as? UIView else {
                continue
            }
            return describe(item, heading: [])
        }
        return ["found": .bool(false)]
    }

    private static func describe(_ view: UIView, heading: UIFocusHeading) -> [String: JSONValue] {
        let frame = view.convert(view.bounds, to: nil)
        return [
            "found": .bool(true),
            "id": .string(viewId(view)),
            "class": .string(String(describing: type(of: view))),
            "identifier": .string(view.accessibilityIdentifier ?? ""),
            "heading": .string(headingName(heading)),
            "x": .number(Double(frame.origin.x)),
            "y": .number(Double(frame.origin.y)),
            "width": .number(Double(frame.size.width)),
            "height": .number(Double(frame.size.height)),
            "timestamp": .number(Date().timeIntervalSince1970)
        ]
    }

    private static func viewId(_ view: UIView) -> String {
        String(ObjectIdentifier(view).hashValue)
    }

    static func buttonName(_ type: UIPress.PressType) -> String? {
        switch type {
        case .menu:
            return "menu"
        case .playPause:
            return "play_pause"
        case .select:
            return "select"
        default:
            return nil
        }
    }

    private static func headingName(_ heading: UIFocusHeading) -> String {
        var parts: [String] = []
        if heading.contains(.up) { parts.append("up") }
        if heading.contains(.down) { parts.append("down") }
        if heading.contains(.left) { parts.append("left") }
        if heading.contains(.right) { parts.append("right") }
        if heading.contains(.next) { parts.append("next") }
        if heading.contains(.previous) { parts.append("previous") }
        return parts.joined(separator: "+")
    }
    #else
    func snapshot() -> [String: JSONValue] {
        ["focused": .object(["found": .bool(false)]), "presses": .array([])]
    }
    #endif
}

#if canImport(UIKit)
private extension UIView {
    static func viewliftInstallFocusMonitor() {
        swap(
            #selector(UIView.shouldUpdateFocus(in:)),
            #selector(UIView.viewliftShouldUpdateFocus(in:))
        )
        swap(
            #selector(UIView.didUpdateFocus(in:with:)),
            #selector(UIView.viewliftDidUpdateFocus(in:with:))
        )
    }

    static func swap(_ original: Selector, _ replacement: Selector) {
        guard let source = class_getInstanceMethod(UIView.self, original),
              let target = class_getInstanceMethod(UIView.self, replacement) else {
            return
        }
        method_exchangeImplementations(source, target)
    }

    @objc func viewliftShouldUpdateFocus(in context: UIFocusUpdateContext) -> Bool {
        let allowed = viewliftShouldUpdateFocus(in: context)
        if !allowed {
            FocusDebugMonitor.shared.noteRejection(self, context: context)
        }
        return allowed
    }

    @objc func viewliftDidUpdateFocus(
        in context: UIFocusUpdateContext,
        with coordinator: UIFocusAnimationCoordinator
    ) {
        viewliftDidUpdateFocus(in: context, with: coordinator)
        FocusDebugMonitor.shared.noteUpdate(context)
    }
}
#endif
#endif
