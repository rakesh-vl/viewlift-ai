#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// One recorded user step. Taps keep the element they hit so replay finds it again by meaning.
struct MacroStep: Equatable {
    var kind: String
    var offset: Double
    var label = ""
    var identifier = ""
    var role = ""
    var pointX = 0.0
    var pointY = 0.0
    var direction = ""
    var press = ""
    var screen = ""

    func json() -> JSONValue {
        var row: [String: JSONValue] = [
            "kind": .string(kind),
            "offset": .number((offset * 1_000).rounded() / 1_000)
        ]
        let text: [(String, String)] = [
            ("label", label), ("identifier", identifier), ("role", role),
            ("direction", direction), ("press", press), ("screen", screen)
        ]
        for (key, value) in text where !value.isEmpty {
            row[key] = .string(value)
        }
        if kind != "press" {
            row["x"] = .number(pointX.rounded())
            row["y"] = .number(pointY.rounded())
        }
        return .object(row)
    }

    init(kind: String, offset: Double) {
        self.kind = kind
        self.offset = offset
    }

    init?(_ value: JSONValue) {
        guard case let .object(row) = value else { return nil }
        let payload = AutomationPayload(row)
        let kind = payload.string("kind")
        guard ["tap", "swipe", "press"].contains(kind) else { return nil }
        self.kind = kind
        offset = max(0, payload.number("offset") ?? 0)
        label = payload.string("label")
        identifier = payload.string("identifier")
        role = payload.string("role")
        pointX = payload.number("x") ?? 0
        pointY = payload.number("y") ?? 0
        direction = payload.string("direction")
        press = payload.string("press")
        screen = payload.string("screen")
    }
}

/// Records taps, swipes, and Apple TV remote presses from `UIApplication.sendEvent`, and replays
/// them through the same automation the agent uses, so SwiftUI and UIKit controls both work.
final class MacroRecorder: @unchecked Sendable {
    static let shared = MacroRecorder()
    static let maxSteps = 500
    static let maxWait = 5.0

    private let lock = NSLock()
    private var recording = false
    private var startedAt = Date()
    private var steps: [MacroStep] = []
    private var open: [ObjectIdentifier: MacroStep] = [:]

    private init() {}

    /// Touch recording rides on the hang observer's `sendEvent` hook, which is always installed.
    func install() {
        HangInteractionTrail.shared.install()
    }

    var isRecording: Bool {
        lock.lock()
        defer { lock.unlock() }
        return recording
    }

    func start() -> [String: JSONValue] {
        lock.lock()
        steps = []
        open = [:]
        startedAt = Date()
        recording = true
        lock.unlock()
        return snapshot()
    }

    func stop() -> [String: JSONValue] {
        lock.lock()
        recording = false
        open = [:]
        lock.unlock()
        return snapshot()
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let copy = steps
        let active = recording
        lock.unlock()
        return [
            "recording": .bool(active),
            "actions": .array(copy.map { $0.json() }),
            "count": .number(Double(copy.count))
        ]
    }

    func elapsed() -> Double {
        lock.lock()
        defer { lock.unlock() }
        return Date().timeIntervalSince(startedAt)
    }

    func begin(id: ObjectIdentifier, step: MacroStep) {
        lock.lock()
        defer { lock.unlock() }
        guard recording else { return }
        open[id] = step
        if open.count > 16 { open.removeAll() }
    }

    /// Finishes the touch: a short move is a tap, a longer one is a swipe in the finger's direction.
    func end(id: ObjectIdentifier, pointX: Double, pointY: Double) {
        lock.lock()
        defer { lock.unlock() }
        guard recording, var step = open.removeValue(forKey: id) else { return }
        let deltaX = pointX - step.pointX
        let deltaY = pointY - step.pointY
        if HangTouchKind.name(distance: hypot(deltaX, deltaY)) == "scroll" {
            step.kind = "swipe"
            step.direction = HangTouchKind.direction(dx: deltaX, dy: deltaY)
        }
        append(step)
    }

    func notePress(_ name: String, screen: String) {
        lock.lock()
        defer { lock.unlock() }
        guard recording else { return }
        var step = MacroStep(kind: "press", offset: Date().timeIntervalSince(startedAt))
        step.press = name
        step.screen = screen
        append(step)
    }

    private func append(_ step: MacroStep) {
        steps.append(step)
        if steps.count > Self.maxSteps {
            steps.removeFirst(steps.count - Self.maxSteps)
        }
    }

    @MainActor
    func replay(_ payload: [String: JSONValue]) async -> [String: JSONValue] {
        guard case let .array(items) = payload["actions"] else {
            return [
                "ok": .bool(false),
                "detail": .string("Pass actions from a recorded macro."),
                "replayed": .number(0)
            ]
        }
        let parsed = items.prefix(Self.maxSteps).compactMap(MacroStep.init)
        var results: [JSONValue] = []
        var replayed = 0
        var previous = parsed.first?.offset ?? 0
        for (index, step) in parsed.enumerated() {
            let wait = max(0.3, min(step.offset - previous, Self.maxWait))
            previous = step.offset
            if index > 0 {
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            }
            let outcome = await MacroPlayer.run(step)
            if outcome.ok { replayed += 1 }
            results.append(.object([
                "index": .number(Double(index)),
                "ok": .bool(outcome.ok),
                "detail": .string(outcome.detail)
            ]))
        }
        return [
            "replayed": .number(Double(replayed)),
            "total": .number(Double(parsed.count)),
            "steps": .array(results)
        ]
    }
}

/// Runs one macro step with the automation services.
@MainActor
enum MacroPlayer {
    static func run(_ step: MacroStep) async -> (ok: Bool, detail: String) {
        #if canImport(UIKit)
        switch step.kind {
        case "press":
            let result = await TVRemoteService.press(AutomationPayload(["press": .string(step.press)]))
            return outcome(result, success: "Pressed \(step.press)")
        case "swipe":
            var payload: [String: JSONValue] = ["direction": .string(step.direction), "fraction": .number(0.5)]
            payload.merge(query(step)) { current, _ in current }
            return outcome(UIAutomationService.scroll(AutomationPayload(payload), fingerDirection: true),
                           success: "Swiped \(step.direction)")
        default:
            return tap(step)
        }
        #else
        _ = step
        return (false, "Replay needs UIKit.")
        #endif
    }

    #if canImport(UIKit)
    private static func tap(_ step: MacroStep) -> (ok: Bool, detail: String) {
        let named = query(step)
        if !named.isEmpty {
            let result = UIAutomationService.tap(AutomationPayload(named))
            if result["tapped"] == .bool(true) {
                return (true, "Tapped \(describe(step))")
            }
        }
        let point: [String: JSONValue] = ["x": .number(step.pointX), "y": .number(step.pointY)]
        return outcome(UIAutomationService.tap(AutomationPayload(point)),
                       success: "Tapped at \(Int(step.pointX)), \(Int(step.pointY))")
    }

    private static func query(_ step: MacroStep) -> [String: JSONValue] {
        if !step.identifier.isEmpty {
            return ["identifier": .string(step.identifier)]
        }
        if !step.label.isEmpty {
            return ["label": .string(step.label)]
        }
        return [:]
    }

    private static func describe(_ step: MacroStep) -> String {
        step.identifier.isEmpty ? "\"\(step.label)\"" : "#\(step.identifier)"
    }

    private static func outcome(_ result: [String: JSONValue], success: String) -> (ok: Bool, detail: String) {
        if result["ok"] == .bool(false) {
            if case let .string(detail) = result["detail"] {
                return (false, detail)
            }
            return (false, "Step failed.")
        }
        return (true, success)
    }

    /// Element under a touch, read at touch-down before the UI reacts to it.
    static func step(at point: CGPoint, offset: Double) -> MacroStep {
        var step = MacroStep(kind: "tap", offset: offset)
        step.pointX = Double(point.x)
        step.pointY = Double(point.y)
        step.screen = AutomationScreen.name()
        if let node = AccessibilityTreeService.node(at: point) {
            step.label = node.info.label
            step.identifier = node.info.identifier
            step.role = node.info.role
        }
        return step
    }
    #endif
}

#if canImport(UIKit)
extension MacroRecorder {
    /// Called for every event on the main thread. Does nothing unless a recording is running.
    func observe(_ event: UIEvent) {
        guard isRecording else { return }
        if event.type == .presses, let presses = event as? UIPressesEvent {
            for press in presses.allPresses where press.phase == .began {
                guard let name = FocusDebugMonitor.buttonName(press.type) else { continue }
                let screen = MainActor.assumeIsolated { AutomationScreen.name() }
                notePress(name, screen: screen)
            }
            return
        }
        guard event.type == .touches, let touches = event.allTouches else { return }
        // Siri Remote touch surface reports indirect touches with no screen position.
        for touch in touches where touch.type != .indirect {
            let point = touch.location(in: nil)
            switch touch.phase {
            case .began:
                let offset = elapsed()
                let step = MainActor.assumeIsolated { MacroPlayer.step(at: point, offset: offset) }
                begin(id: ObjectIdentifier(touch), step: step)
            case .ended:
                end(id: ObjectIdentifier(touch), pointX: Double(point.x), pointY: Double(point.y))
            default:
                break
            }
        }
    }
}
#endif
#endif
