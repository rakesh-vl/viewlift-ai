#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime
#if canImport(UIKit)
import UIKit
#endif

enum HangTouchKind {
    static let scrollDistance = 12.0

    static func name(distance: Double) -> String {
        distance >= scrollDistance ? "scroll" : "tap"
    }

    static func direction(dx: Double, dy: Double) -> String {
        guard name(distance: hypot(dx, dy)) == "scroll" else { return "" }
        if abs(dy) >= abs(dx) {
            return dy < 0 ? "up" : "down"
        }
        return dx < 0 ? "left" : "right"
    }
}

enum HangFrameFocus {
    static func suspect(
        frames: [[String: JSONValue]],
        executable: String
    ) -> [String: JSONValue]? {
        let app = frames.first { isApp($0, executable: executable) }
        return app ?? frames.first { frameFile($0) != nil }
    }

    static func isApp(_ frame: [String: JSONValue], executable: String) -> Bool {
        guard !executable.isEmpty else { return false }
        guard case let .string(image) = frame["image"] else { return false }
        return image == executable
    }
}

enum HangReport {
    static let jpegByteLimit = 32_000

    static func make(duration: TimeInterval, symbols: [String], stackSource: String? = nil) -> [String: JSONValue] {
        let resolved = resolve(symbols: symbols, stackSource: stackSource)
        let frames = CrashHangRecorder.parseFrames(resolved.symbols)
        let executable = executableName()
        var event = identity(duration: duration, source: resolved.source, executable: executable)
        event["frames"] = .array(frames.map(JSONValue.object))
        if let focus = HangFrameFocus.suspect(frames: frames, executable: executable) {
            event["suspect"] = .object(focus)
        }
        attachScreen(&event)
        let now = Date()
        event["actions"] = .array(HangInteractionTrail.shared.recent(before: now))
        event["console"] = .array(HangConsoleBuffer.shared.recent(before: now, seconds: max(8, duration + 2)))
        attachScreenshot(&event)
        return event
    }

    static func dropOldScreenshots(_ events: inout [[String: JSONValue]], budget: Int) {
        var total = events.reduce(0) { $0 + screenshotBytes($1) }
        var index = 0
        while total > budget, index < events.count {
            let used = screenshotBytes(events[index])
            if used > 0 {
                events[index].removeValue(forKey: "screenshot")
                events[index]["screenshot_note"] = .string(
                    "Omitted so newer hangs can keep their screenshot."
                )
                total -= used
            }
            index += 1
        }
    }

    private static func resolve(symbols: [String], stackSource: String?) -> (symbols: [String], source: String) {
        if !symbols.isEmpty {
            return (symbols, stackSource ?? "blocked")
        }
        return (Thread.callStackSymbols, "recovered")
    }

    private static func identity(
        duration: TimeInterval,
        source: String,
        executable: String
    ) -> [String: JSONValue] {
        [
            "id": .string(UUID().uuidString),
            "kind": .string("hang"),
            "timestamp": .number(Date().timeIntervalSince1970),
            "duration_ms": .number(duration * 1_000),
            "thread": .string("main"),
            "stack_source": .string(source),
            "executable": .string(executable)
        ]
    }

    private static func attachScreen(_ event: inout [String: JSONValue]) {
        let screen = HangScreen.current()
        event["screen_class"] = .string(screen.className)
        event["screen_title"] = .string(screen.title)
    }

    private static func attachScreenshot(_ event: inout [String: JSONValue]) {
        guard let encoded = HangScreenshot.jpegBase64(maxBytes: jpegByteLimit) else { return }
        event["screenshot"] = .object([
            "mime": .string("image/jpeg"),
            "base64": .string(encoded)
        ])
    }

    private static func executableName() -> String {
        Bundle.main.executableURL?.lastPathComponent ?? ""
    }

    private static func screenshotBytes(_ event: [String: JSONValue]) -> Int {
        guard case let .object(shot) = event["screenshot"],
              case let .string(text) = shot["base64"] else {
            return 0
        }
        return text.utf8.count
    }
}

private func frameFile(_ frame: [String: JSONValue]) -> String? {
    guard case let .string(file) = frame["file"], !file.isEmpty else { return nil }
    return file
}

enum HangScreen {
    static func current() -> (className: String, title: String) {
        #if canImport(UIKit)
        let controller = top(keyWindow()?.rootViewController)
        let name = controller.map { String(describing: type(of: $0)) } ?? ""
        let title = controller?.title ?? controller?.navigationItem.title ?? ""
        return (name, String(title.prefix(80)))
        #else
        return ("", "")
        #endif
    }

    #if canImport(UIKit)
    /// UIKit is main-thread only; watchdog threads get nil instead of racing it.
    static func keyWindow() -> UIWindow? {
        guard Thread.isMainThread else { return nil }
        return MainActor.assumeIsolated { AutomationScreen.keyWindow() }
    }

    private static func top(_ controller: UIViewController?) -> UIViewController? {
        if let navigation = controller as? UINavigationController {
            return top(navigation.visibleViewController)
        }
        if let tabs = controller as? UITabBarController {
            return top(tabs.selectedViewController)
        }
        if let presented = controller?.presentedViewController {
            return top(presented)
        }
        return controller
    }
    #endif
}

enum HangScreenshot {
    static func jpegBase64(maxBytes: Int) -> String? {
        #if canImport(UIKit)
        guard let window = HangScreen.keyWindow(), let image = scaled(window) else { return nil }
        for quality in [0.5, 0.35, 0.2] as [CGFloat] {
            guard let data = image.jpegData(compressionQuality: quality),
                  data.count <= maxBytes else {
                continue
            }
            return data.base64EncodedString()
        }
        return nil
        #else
        _ = maxBytes
        return nil
        #endif
    }

    #if canImport(UIKit)
    private static func scaled(_ window: UIWindow) -> UIImage? {
        guard let full = WindowSnapshot.image(of: window, scale: 1) else { return nil }
        let width = min(window.bounds.width, 280)
        let height = width * window.bounds.height / max(window.bounds.width, 1)
        let size = CGSize(width: width, height: height)
        return UIGraphicsImageRenderer(size: size).image { _ in
            full.draw(in: CGRect(origin: .zero, size: size))
        }
    }
    #endif
}

final class HangInteractionTrail: @unchecked Sendable {
    static let shared = HangInteractionTrail()

    private let lock = NSLock()
    private var scheduled = false
    private var actions: [HangTouchRecord] = []
    private var open: [ObjectIdentifier: HangTouchStart] = [:]

    private init() {}

    func install() {
        #if canImport(UIKit)
        lock.lock()
        if scheduled {
            lock.unlock()
            return
        }
        scheduled = true
        lock.unlock()
        DispatchQueue.main.async {
            UIApplication.viewliftInstallHangTouches()
        }
        #endif
    }

    func noteBegan(id: ObjectIdentifier, point: (Double, Double), viewClass: String, label: String, screen: String) {
        lock.lock()
        open[id] = HangTouchStart(
            originX: point.0,
            originY: point.1,
            viewClass: viewClass,
            label: label,
            screen: screen,
            time: Date().timeIntervalSince1970
        )
        if open.count > 32 {
            open.removeAll()
        }
        lock.unlock()
    }

    func noteEnded(id: ObjectIdentifier, point: (Double, Double)) {
        lock.lock()
        guard let start = open.removeValue(forKey: id) else {
            lock.unlock()
            return
        }
        let dx = point.0 - start.originX
        let dy = point.1 - start.originY
        let distance = hypot(dx, dy)
        actions.append(HangTouchRecord(
            time: start.time,
            kind: HangTouchKind.name(distance: distance),
            direction: HangTouchKind.direction(dx: dx, dy: dy),
            target: start.viewClass,
            screen: start.screen,
            label: start.label,
            distance: distance
        ))
        if actions.count > 24 {
            actions.removeFirst(actions.count - 24)
        }
        lock.unlock()
    }

    func recent(before date: Date, limit: Int = 12) -> [JSONValue] {
        lock.lock()
        let copy = actions
        lock.unlock()
        let now = date.timeIntervalSince1970
        return copy.suffix(max(0, limit)).map { record in
            var row: [String: JSONValue] = [
                "kind": .string(record.kind),
                "target_class": .string(record.target),
                "screen_class": .string(record.screen),
                "ago_ms": .number(max(0, (now - record.time) * 1_000)),
                "distance": .number(record.distance)
            ]
            if !record.direction.isEmpty {
                row["direction"] = .string(record.direction)
            }
            if !record.label.isEmpty {
                row["label"] = .string(record.label)
            }
            return .object(row)
        }
    }
}

private struct HangTouchStart {
    var originX: Double
    var originY: Double
    var viewClass: String
    var label: String
    var screen: String
    var time: TimeInterval
}

private struct HangTouchRecord {
    var time: TimeInterval
    var kind: String
    var direction: String
    var target: String
    var screen: String
    var label: String
    var distance: Double
}

#if canImport(UIKit)
private extension UIApplication {
    static func viewliftInstallHangTouches() {
        guard
            let original = class_getInstanceMethod(UIApplication.self, #selector(UIApplication.sendEvent(_:))),
            let replacement = class_getInstanceMethod(
                UIApplication.self,
                #selector(UIApplication.viewliftHangSendEvent(_:))
            )
        else {
            return
        }
        method_exchangeImplementations(original, replacement)
    }

    @objc func viewliftHangSendEvent(_ event: UIEvent) {
        HangTouchObserver.record(event)
        viewliftHangSendEvent(event)
    }
}

private enum HangTouchObserver {
    static func record(_ event: UIEvent) {
        if event.type == .presses {
            noteReplayPresses(event)
            FocusDebugMonitor.shared.notePresses(event)
            return
        }
        guard event.type == .touches, let touches = event.allTouches else { return }
        for touch in touches {
            let id = ObjectIdentifier(touch)
            noteReplay(touch)
            switch touch.phase {
            case .began:
                HangInteractionTrail.shared.noteBegan(
                    id: id,
                    point: (Double(touch.location(in: nil).x), Double(touch.location(in: nil).y)),
                    viewClass: meaningfulClass(touch.view),
                    label: String((touch.view?.accessibilityLabel ?? "").prefix(80)),
                    screen: HangScreen.current().className
                )
            case .ended, .cancelled:
                let point = touch.location(in: nil)
                HangInteractionTrail.shared.noteEnded(id: id, point: (Double(point.x), Double(point.y)))
            default:
                break
            }
        }
    }

    private static func noteReplay(_ touch: UITouch) {
        let phase: String
        switch touch.phase {
        case .began: phase = "began"
        case .ended, .cancelled: phase = "ended"
        default: return
        }
        guard let bounds = touch.window?.bounds, bounds.width > 0, bounds.height > 0 else { return }
        let point = touch.location(in: nil)
        let pointX = Double(point.x / bounds.width)
        let pointY = Double(point.y / bounds.height)
        LeakReplayRecorder.shared.noteTouch(phase: phase, pointX: pointX, pointY: pointY)
        let label = phase == "began" ? String((touch.view?.accessibilityLabel ?? "").prefix(40)) : ""
        CrashReplayRecorder.shared.noteTouch(
            CrashReplayTouch(phase: phase, pointX: pointX, pointY: pointY, label: label)
        )
    }

    private static func noteReplayPresses(_ event: UIEvent) {
        guard let presses = event as? UIPressesEvent else { return }
        for press in presses.allPresses where press.phase == .began {
            guard let name = FocusDebugMonitor.buttonName(press.type) else { continue }
            CrashReplayRecorder.shared.notePress(name)
            CrashContextWriter.shared.notePress(name)
        }
    }

    private static func meaningfulClass(_ view: UIView?) -> String {
        var current = view
        var fallback = ""
        while let item = current {
            let name = String(describing: type(of: item))
            if fallback.isEmpty {
                fallback = name
            }
            if !name.hasPrefix("UI"), !name.hasPrefix("_") {
                return name
            }
            current = item.superview
        }
        return fallback
    }
}
#endif
#endif
