#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import CoreGraphics
import Foundation

/// Typed reads from a command payload. Missing or mistyped keys fall back to defaults instead of failing.
struct AutomationPayload {
    let raw: [String: JSONValue]

    init(_ raw: [String: JSONValue]) {
        self.raw = raw
    }

    /// Trimmed text. Numbers are accepted so `"index": 2` and `"index": "2"` read the same.
    func string(_ key: String) -> String {
        switch raw[key] {
        case let .string(value):
            return value.trimmingCharacters(in: .whitespacesAndNewlines)
        case let .number(value) where value.isFinite:
            return value.rounded() == value && abs(value) < 1e15 ? String(Int(value)) : String(value)
        default:
            return ""
        }
    }

    /// Text exactly as sent, for typing.
    func text(_ key: String) -> String {
        if case let .string(value) = raw[key] { return value }
        return ""
    }

    func number(_ key: String) -> Double? {
        switch raw[key] {
        case let .number(value) where value.isFinite:
            return value
        case let .string(value):
            return Double(value.trimmingCharacters(in: .whitespaces)).flatMap { $0.isFinite ? $0 : nil }
        default:
            return nil
        }
    }

    func int(_ key: String, fallback: Int, range: ClosedRange<Int>) -> Int {
        guard let value = number(key) else { return fallback }
        return min(max(Int(safe: value.rounded()), range.lowerBound), range.upperBound)
    }

    func bool(_ key: String) -> Bool {
        switch raw[key] {
        case let .bool(value):
            return value
        case let .number(value):
            return value != 0
        case let .string(value):
            return ["true", "yes", "1"].contains(value.lowercased())
        default:
            return false
        }
    }
}

/// One accessibility element as the agent sees it. `frame` is in window points;
/// `screenFrame` is in screen points, which is what host taps use under Stage Manager.
struct AccessibilityElementInfo: Equatable {
    var role: String
    var label: String
    var identifier: String
    var value: String
    var frame: CGRect
    var enabled = true
    var focused = false
    var selected = false
    var viewId = ""
    var windowId = ""
    var sceneId = ""
    var screenFrame: CGRect?

    var center: CGPoint {
        CGPoint(x: frame.midX, y: frame.midY)
    }

    func json() -> JSONValue {
        var fields: [String: JSONValue] = [
            "role": .string(role),
            "label": .string(label),
            "identifier": .string(identifier),
            "value": .string(value),
            "frame": Self.json(frame),
            "enabled": .bool(enabled)
        ]
        if focused { fields["focused"] = .bool(true) }
        if selected { fields["selected"] = .bool(true) }
        if !viewId.isEmpty { fields["view_id"] = .string(viewId) }
        if !windowId.isEmpty { fields["window_id"] = .string(windowId) }
        if !sceneId.isEmpty { fields["scene_id"] = .string(sceneId) }
        if let screenFrame, screenFrame != frame { fields["screen_frame"] = Self.json(screenFrame) }
        return .object(fields)
    }

    private static func json(_ rect: CGRect) -> JSONValue {
        .object([
            "x": .number(rounded(rect.origin.x)),
            "y": .number(rounded(rect.origin.y)),
            "width": .number(rounded(rect.width)),
            "height": .number(rounded(rect.height))
        ])
    }

    private static func rounded(_ value: CGFloat) -> Double {
        (Double(value) * 10).rounded() / 10
    }
}

/// Finds elements the way a tester names them: exact label, identifier, role, or text anywhere.
struct ElementQuery: Equatable {
    var label = ""
    var identifier = ""
    var text = ""
    var role = ""
    var viewId = ""
    /// Limits the search to one window or scene on iPad multi-window and external displays.
    var windowId = ""
    var sceneId = ""
    var index = 0

    init(label: String = "", identifier: String = "", text: String = "", role: String = "", viewId: String = "") {
        self.label = label
        self.identifier = identifier
        self.text = text
        self.role = role
        self.viewId = viewId
    }

    init(payload: AutomationPayload) {
        label = payload.string("label")
        identifier = payload.string("identifier")
        text = payload.string("text")
        role = payload.string("role")
        viewId = payload.string("view_id")
        windowId = payload.string("window_id")
        sceneId = payload.string("scene_id")
        index = payload.int("index", fallback: 0, range: 0...500)
    }

    var isEmpty: Bool {
        label.isEmpty && identifier.isEmpty && text.isEmpty && role.isEmpty && viewId.isEmpty
    }

    func matches(_ element: AccessibilityElementInfo) -> Bool {
        if !viewId.isEmpty, element.viewId != viewId { return false }
        if !windowId.isEmpty, element.windowId != windowId { return false }
        if !sceneId.isEmpty, element.sceneId != sceneId { return false }
        if !identifier.isEmpty, element.identifier != identifier { return false }
        if !label.isEmpty, element.label.lowercased() != label.lowercased() { return false }
        if !role.isEmpty, !element.role.contains(Self.normalizedRole(role)) { return false }
        if !text.isEmpty {
            let haystack = [element.label, element.identifier, element.value].joined(separator: " ").lowercased()
            if !haystack.contains(text.lowercased()) { return false }
        }
        return true
    }

    func json() -> JSONValue {
        var fields: [String: JSONValue] = [:]
        if !label.isEmpty { fields["label"] = .string(label) }
        if !identifier.isEmpty { fields["identifier"] = .string(identifier) }
        if !text.isEmpty { fields["text"] = .string(text) }
        if !role.isEmpty { fields["role"] = .string(role) }
        if !viewId.isEmpty { fields["view_id"] = .string(viewId) }
        if !windowId.isEmpty { fields["window_id"] = .string(windowId) }
        if !sceneId.isEmpty { fields["scene_id"] = .string(sceneId) }
        return .object(fields)
    }

    private static func normalizedRole(_ raw: String) -> String {
        raw.lowercased().replacingOccurrences(of: " ", with: "")
    }
}

/// Agent-facing role names. They match the simulator path so one query works on both.
enum AccessibilityRole {
    private static let traitRoles: [(trait: String, role: String)] = [
        ("searchfield", "searchfield"),
        ("toggle", "switch"),
        ("button", "button"),
        ("link", "link"),
        ("keyboardkey", "button"),
        ("adjustable", "slider"),
        ("header", "heading"),
        ("tabbar", "tab"),
        ("image", "image"),
        ("statictext", "statictext")
    ]

    /// `kind` comes from the UIKit class (text field, switch, cell) and wins over traits.
    static func resolve(kind: String, traits: Set<String>) -> String {
        if !kind.isEmpty { return kind }
        return traitRoles.first { traits.contains($0.trait) }?.role ?? "element"
    }
}

enum SwipeDirection: String, CaseIterable {
    case up, down, left, right

    /// A finger swipe moves content the other way: swiping up shows what is below.
    var scrollDirection: SwipeDirection {
        switch self {
        case .up: return .down
        case .down: return .up
        case .left: return .right
        case .right: return .left
        }
    }
}

struct ScrollInsets: Equatable {
    var top: CGFloat = 0
    var left: CGFloat = 0
    var bottom: CGFloat = 0
    var right: CGFloat = 0
}

/// Offsets for one scroll view, independent of UIKit so the math can be tested.
struct ScrollMetrics: Equatable {
    var offset: CGPoint
    var viewport: CGSize
    var minOffset: CGPoint
    var maxOffset: CGPoint

    init(offset: CGPoint, contentSize: CGSize, viewport: CGSize, insets: ScrollInsets = ScrollInsets()) {
        self.offset = offset
        self.viewport = viewport
        minOffset = CGPoint(x: -insets.left, y: -insets.top)
        maxOffset = CGPoint(
            x: max(-insets.left, contentSize.width + insets.right - viewport.width),
            y: max(-insets.top, contentSize.height + insets.bottom - viewport.height)
        )
    }

    func canScroll(_ direction: SwipeDirection) -> Bool {
        target(direction, fraction: 0.8) != offset
    }

    /// Moves `fraction` of a page in the content direction, clamped to the content edges.
    func target(_ direction: SwipeDirection, fraction: Double) -> CGPoint {
        let share = CGFloat(min(max(fraction, 0.05), 1))
        var next = offset
        switch direction {
        case .down: next.y += viewport.height * share
        case .up: next.y -= viewport.height * share
        case .right: next.x += viewport.width * share
        case .left: next.x -= viewport.width * share
        }
        next.x = min(max(next.x, minOffset.x), maxOffset.x)
        next.y = min(max(next.y, minOffset.y), maxOffset.y)
        return next
    }
}

/// Picks the next focus target in a direction, like the tvOS focus engine does for a remote swipe.
enum FocusNavigator {
    static func best(from current: CGRect, candidates: [CGRect], direction: SwipeDirection) -> Int? {
        var bestIndex: Int?
        var bestScore = CGFloat.greatestFiniteMagnitude
        for (index, frame) in candidates.enumerated() {
            guard let score = score(from: current, to: frame, direction: direction), score < bestScore else {
                continue
            }
            bestScore = score
            bestIndex = index
        }
        return bestIndex
    }

    /// Distance along the direction plus a penalty for drifting sideways. Nil when the frame is behind.
    private static func score(from current: CGRect, to frame: CGRect, direction: SwipeDirection) -> CGFloat? {
        let deltaX = frame.midX - current.midX
        let deltaY = frame.midY - current.midY
        let primary: CGFloat
        let lateral: CGFloat
        switch direction {
        case .right: (primary, lateral) = (deltaX, abs(deltaY))
        case .left: (primary, lateral) = (-deltaX, abs(deltaY))
        case .down: (primary, lateral) = (deltaY, abs(deltaX))
        case .up: (primary, lateral) = (-deltaY, abs(deltaX))
        }
        guard primary > 1 else { return nil }
        return primary + lateral * 2
    }
}
#endif
