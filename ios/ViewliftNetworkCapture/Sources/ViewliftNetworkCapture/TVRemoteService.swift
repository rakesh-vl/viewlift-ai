#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import AVFoundation
import UIKit

/// Apple TV remote presses through the public focus API. Directions move focus to the nearest
/// focusable item, select activates it, and menu goes back one screen.
@MainActor
enum TVRemoteService {
    static let presses = ["up", "down", "left", "right", "select", "menu", "playpause"]

    static func press(_ payload: AutomationPayload) async -> [String: JSONValue] {
        #if os(tvOS)
        let press = payload.string("press").lowercased()
        guard presses.contains(press) else {
            return ["ok": .bool(false), "detail": .string("press must be one of \(presses.joined(separator: ", "))")]
        }
        let count = payload.int("repeat", fallback: 1, range: 1...20)
        var done = 0
        for _ in 0..<count {
            guard perform(press) else { break }
            done += 1
            try? await Task.sleep(nanoseconds: 350_000_000)
        }
        var result: [String: JSONValue] = [
            "press": .string(press),
            "done": .number(Double(done)),
            "focused": FocusDebugMonitor.shared.snapshot()["focused"] ?? .null,
            "screen": .string(AutomationScreen.name())
        ]
        if done < count {
            result["ok"] = .bool(false)
            result["detail"] = .string(done == 0 ? "Nothing to do for \(press) here." : "Stopped at an edge.")
        }
        return result
        #else
        _ = payload
        return ["ok": .bool(false), "detail": .string("ui.remote is tvOS only. Use ui.tap and ui.swipe on iOS.")]
        #endif
    }

    #if os(tvOS)
    private static func perform(_ press: String) -> Bool {
        if let direction = SwipeDirection(rawValue: press) {
            return moveFocus(direction)
        }
        switch press {
        case "select":
            return select()
        case "menu":
            return goBack()
        default:
            return togglePlayback()
        }
    }

    private static func moveFocus(_ direction: SwipeDirection) -> Bool {
        guard let window = AutomationScreen.keyWindow(),
              let system = UIFocusSystem.focusSystem(for: window),
              let current = system.focusedItem,
              let container = window.focusItemContainer else {
            return false
        }
        let candidates = container
            .focusItems(in: window.bounds)
            .filter { $0.canBecomeFocused && $0 !== current }
        let frames = candidates.map { frame(of: $0, in: window) }
        guard let index = FocusNavigator.best(
            from: frame(of: current, in: window),
            candidates: frames,
            direction: direction
        ) else {
            return false
        }
        system.requestFocusUpdate(to: candidates[index])
        system.updateFocusIfNeeded()
        return true
    }

    private static func select() -> Bool {
        guard let window = AutomationScreen.keyWindow(),
              let item = UIFocusSystem.focusSystem(for: window)?.focusedItem else {
            return false
        }
        if let view = item as? UIView, UIActivation.activateView(view) != nil {
            return true
        }
        return (item as? NSObject)?.accessibilityActivate() ?? false
    }

    private static func goBack() -> Bool {
        guard let top = AutomationScreen.topController() else { return false }
        if top.presentingViewController != nil {
            top.dismiss(animated: true)
            return true
        }
        if let navigation = top.navigationController, navigation.viewControllers.count > 1 {
            navigation.popViewController(animated: true)
            return true
        }
        return false
    }

    private static func togglePlayback() -> Bool {
        guard let player = PlaybackDebugService.shared.boundPlayer else { return false }
        if player.timeControlStatus == .paused {
            player.play()
        } else {
            player.pause()
        }
        return true
    }

    private static func frame(of item: UIFocusItem, in window: UIWindow) -> CGRect {
        if let view = item as? UIView {
            return view.convert(view.bounds, to: window)
        }
        guard let space = item.parentFocusEnvironment?.focusItemContainer?.coordinateSpace else {
            return item.frame
        }
        return space.convert(item.frame, to: window)
    }
    #endif
}
#endif
#endif
