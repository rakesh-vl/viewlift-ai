import XCTest

/// One accessibility element in the shape the SDK reports, so `screen_map` reads both alike.
struct RunnerElement {
    let role: String
    let label: String
    let identifier: String
    let value: String
    let frame: CGRect
    let enabled: Bool
    let focused: Bool

    var dictionary: [String: Any] {
        [
            "role": role,
            "label": label,
            "identifier": identifier,
            "value": value,
            "frame": [
                "x": Double(frame.origin.x).rounded(),
                "y": Double(frame.origin.y).rounded(),
                "width": Double(frame.width).rounded(),
                "height": Double(frame.height).rounded()
            ],
            "enabled": enabled,
            "focused": focused
        ]
    }
}

/// Element filter from a command payload: exact label or identifier, role, or text anywhere.
struct RunnerQuery {
    let label: String
    let identifier: String
    let text: String
    let role: String

    init(_ payload: [String: Any]) {
        label = RunnerQuery.string(payload["label"])
        identifier = RunnerQuery.string(payload["identifier"])
        text = RunnerQuery.string(payload["text"])
        role = RunnerQuery.string(payload["role"]).lowercased()
    }

    var isEmpty: Bool { label.isEmpty && identifier.isEmpty && text.isEmpty && role.isEmpty }

    func matches(_ element: RunnerElement) -> Bool {
        if !label.isEmpty, element.label != label { return false }
        if !identifier.isEmpty, element.identifier != identifier { return false }
        if !role.isEmpty, element.role.lowercased() != role { return false }
        if !text.isEmpty {
            let haystack = [element.label, element.identifier, element.value]
            return haystack.contains { $0.localizedCaseInsensitiveContains(text) }
        }
        return true
    }

    static func string(_ raw: Any?) -> String {
        switch raw {
        case let text as String: return text.trimmingCharacters(in: .whitespacesAndNewlines)
        case let number as NSNumber: return number.stringValue
        default: return ""
        }
    }
}

enum RunnerElements {
    /// Labelled or interactive elements in document order.
    static func collect(_ app: XCUIApplication, limit: Int) throws -> [RunnerElement] {
        var found: [RunnerElement] = []
        var stack: [XCUIElementSnapshot] = [try app.snapshot()]
        while let node = stack.popLast(), found.count < limit {
            if let element = element(from: node) {
                found.append(element)
            }
            stack.append(contentsOf: node.children.reversed())
        }
        return found
    }

    static func first(_ app: XCUIApplication, query: RunnerQuery, index: Int) throws -> RunnerElement? {
        let matches = try collect(app, limit: 2_000).filter(query.matches)
        guard matches.indices.contains(index) else { return nil }
        return matches[index]
    }

    private static func element(from node: XCUIElementSnapshot) -> RunnerElement? {
        let role = roleName(node.elementType)
        let value = node.value.map { "\($0)" } ?? ""
        let labelled = !node.label.isEmpty || !node.identifier.isEmpty || !value.isEmpty
        guard labelled || interactiveRoles.contains(role), !node.frame.isEmpty else { return nil }
        return RunnerElement(
            role: role,
            label: node.label,
            identifier: node.identifier,
            value: value,
            frame: node.frame,
            enabled: node.isEnabled,
            focused: hasFocus(node)
        )
    }

    private static func hasFocus(_ node: XCUIElementSnapshot) -> Bool {
        #if os(tvOS)
        return node.hasFocus
        #else
        return false
        #endif
    }

    private static let interactiveRoles: Set<String> = [
        "button", "link", "textField", "secureTextField", "searchField", "textView", "switch",
        "toggle", "slider", "cell", "tab", "segmentedControl", "menuItem"
    ]

    private static let roleNames: [XCUIElement.ElementType: String] = [
        .button: "button", .link: "link", .staticText: "staticText", .textField: "textField",
        .secureTextField: "secureTextField", .searchField: "searchField", .textView: "textView",
        .switch: "switch", .toggle: "toggle", .slider: "slider", .cell: "cell", .tab: "tab",
        .tabBar: "tabBar", .navigationBar: "navigationBar", .image: "image", .icon: "icon",
        .segmentedControl: "segmentedControl", .menuItem: "menuItem", .alert: "alert",
        .collectionView: "collectionView", .table: "table", .scrollView: "scrollView",
        .webView: "webView", .window: "window", .application: "application", .other: "other"
    ]

    static func roleName(_ type: XCUIElement.ElementType) -> String {
        roleNames[type] ?? "other"
    }
}
