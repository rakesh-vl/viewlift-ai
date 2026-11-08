#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Scenes, windows, and the screen name agents see in `ui.accessibility_tree`.
/// Every SDK reader looks windows up here so iPad multi-window, Stage Manager,
/// external displays, and a process without an application object behave the same.
@MainActor
enum AutomationScreen {
    /// `UIApplication.shared` is nil in hostless test bundles and app extensions.
    static var application: UIApplication? {
        let selector = NSSelectorFromString("sharedApplication")
        guard UIApplication.responds(to: selector) else { return nil }
        return UIApplication.perform(selector)?.takeUnretainedValue() as? UIApplication
    }

    /// Window scenes the user can see. Falls back to every scene while the app is fully backgrounded.
    static func scenes() -> [UIWindowScene] {
        guard let application else { return [] }
        let all = application.connectedScenes.compactMap { $0 as? UIWindowScene }
        let visible = all.filter { $0.activationState != .background }
        return visible.isEmpty ? all : visible
    }

    /// Every window in the visible scenes, hidden ones included.
    static func allWindows() -> [UIWindow] {
        scenes().flatMap(\.windows)
    }

    /// Visible app windows, key window first, then by window level.
    static func windows() -> [UIWindow] {
        let all = allWindows().filter { !$0.isHidden && !isSystemWindow($0) }
        return all.sorted { lhs, rhs in
            lhs.isKeyWindow != rhs.isKeyWindow ? lhs.isKeyWindow : lhs.windowLevel > rhs.windowLevel
        }
    }

    static func keyWindow() -> UIWindow? {
        windows().first
    }

    /// The window named by `window_id`, else the key window of the scene named by `scene_id`,
    /// else the key window. Unknown ids return nil so a stale id never targets the wrong window.
    static func window(windowId: String, sceneId: String) -> UIWindow? {
        let candidates = windows()
        if !windowId.isEmpty {
            return candidates.first { self.windowId($0) == windowId }
        }
        if !sceneId.isEmpty {
            return candidates.first { self.sceneId($0.windowScene) == sceneId }
        }
        return candidates.first
    }

    /// Stable id for a scene within this process.
    static func sceneId(_ scene: UIScene?) -> String {
        scene?.session.persistentIdentifier ?? ""
    }

    /// Stable id for a window within this process.
    static func windowId(_ window: UIWindow) -> String {
        String(ObjectIdentifier(window).hashValue)
    }

    /// Converts a window-space rect to the screen space host taps use under Stage Manager.
    static func screenFrame(_ rect: CGRect, in window: UIWindow) -> CGRect {
        window.convert(rect, to: window.screen.coordinateSpace)
    }

    static func topController() -> UIViewController? {
        guard var current = keyWindow()?.rootViewController else { return nil }
        while true {
            if let presented = current.presentedViewController, !presented.isBeingDismissed {
                current = presented
            } else if let navigation = current as? UINavigationController,
                      let visible = navigation.visibleViewController, visible !== current {
                current = visible
            } else if let tabs = current as? UITabBarController, let selected = tabs.selectedViewController {
                current = selected
            } else {
                break
            }
        }
        return current
    }

    static func name() -> String {
        guard let controller = topController() else { return "" }
        let type = String(describing: Swift.type(of: controller))
        let base = type.split(separator: "<").first.map(String.init) ?? type
        let title = controller.navigationItem.title ?? controller.title ?? ""
        return title.isEmpty ? base : "\(base) (\(String(title.prefix(60))))"
    }

    private static func isSystemWindow(_ window: UIWindow) -> Bool {
        let name = String(describing: type(of: window))
        return name.contains("TextEffectsWindow") || name.contains("RemoteKeyboardWindow")
    }
}
#endif
#endif
