import XCTest

enum RunnerCommandError: Error, CustomStringConvertible {
    case unsupported(String)
    case notFound
    case badPayload(String)

    var description: String {
        switch self {
        case let .unsupported(name): return "\(name) is not supported by the UI runner on this platform."
        case .notFound: return "No element matched."
        case let .badPayload(detail): return detail
        }
    }
}

/// Runs console UI commands against the target app through XCUITest.
final class RunnerCommands {
    let app: XCUIApplication

    init(bundleID: String) {
        app = XCUIApplication(bundleIdentifier: bundleID)
    }

    func execute(name: String, payload: [String: Any]) throws -> [String: Any] {
        switch name {
        case "ui.accessibility_tree", "ui.find":
            return try tree(payload)
        case "ui.wait_for":
            return try waitFor(payload)
        case "ui.screenshot":
            return screenshot()
        case "ui.type_text":
            return try typeText(payload)
        case "ui.remote":
            return try remote(payload)
        case "app.launch":
            app.activate()
            return ["launched": true, "state": stateName]
        case "app.terminate":
            app.terminate()
            return ["terminated": true]
        default:
            return try gesture(name: name, payload: payload)
        }
    }

    var stateName: String {
        switch app.state {
        case .runningForeground: return "foreground"
        case .runningBackground, .runningBackgroundSuspended: return "background"
        case .notRunning: return "not running"
        default: return "unknown"
        }
    }

    private func tree(_ payload: [String: Any]) throws -> [String: Any] {
        let limit = min(max(Int(RunnerQuery.string(payload["limit"])) ?? 150, 1), 1_000)
        let query = RunnerQuery(payload)
        var elements = try RunnerElements.collect(app, limit: query.isEmpty ? limit : 2_000)
        if !query.isEmpty {
            elements = Array(elements.filter(query.matches).prefix(limit))
        }
        var out: [String: Any] = [
            "elements": elements.map(\.dictionary),
            "count": elements.count,
            "screen": app.navigationBars.firstMatch.identifier,
            "source": "xcuitest"
        ]
        if let focused = elements.first(where: \.focused) {
            out["focused"] = focused.dictionary
        }
        return out
    }

    private func waitFor(_ payload: [String: Any]) throws -> [String: Any] {
        let query = RunnerQuery(payload)
        guard !query.isEmpty else { throw RunnerCommandError.badPayload("Pass label, identifier, text, or role.") }
        let gone = (payload["gone"] as? Bool) ?? false
        let timeout = min(max(Double(RunnerQuery.string(payload["timeout"])) ?? 5, 0.2), 20)
        let deadline = Date().addingTimeInterval(timeout)
        let started = Date()
        repeat {
            let match = try RunnerElements.first(app, query: query, index: 0)
            if (match != nil) != gone {
                return [
                    "found": match != nil,
                    "element": match?.dictionary ?? NSNull(),
                    "waited_ms": Int(Date().timeIntervalSince(started) * 1_000)
                ]
            }
            Thread.sleep(forTimeInterval: 0.3)
        } while Date() < deadline
        return ["ok": false, "detail": "timed out", "waited_ms": Int(timeout * 1_000)]
    }

    private func screenshot() -> [String: Any] {
        let shot = XCUIScreen.main.screenshot()
        return ["format": "png", "image_base64": shot.pngRepresentation.base64EncodedString()]
    }

    private func typeText(_ payload: [String: Any]) throws -> [String: Any] {
        guard let text = payload["text"] as? String else { throw RunnerCommandError.badPayload("Pass text.") }
        let query = RunnerQuery(payload)
        if !query.isEmpty {
            try focus(query)
        }
        if (payload["replace"] as? Bool) == true {
            clearFocusedField()
        }
        app.typeText(text)
        if (payload["submit"] as? Bool) == true {
            app.typeText("\n")
        }
        return ["typed": text.count]
    }

    private func clearFocusedField() {
        let field = app.descendants(matching: .any).element(matching: NSPredicate(format: "hasKeyboardFocus == true"))
        guard field.exists, let current = field.value as? String, !current.isEmpty else { return }
        app.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
    }

    private func remote(_ payload: [String: Any]) throws -> [String: Any] {
        #if os(tvOS)
        let presses: [String: XCUIRemote.Button] = [
            "up": .up, "down": .down, "left": .left, "right": .right,
            "select": .select, "menu": .menu, "playpause": .playPause, "home": .home
        ]
        let name = RunnerQuery.string(payload["press"]).lowercased()
        guard let button = presses[name] else { throw RunnerCommandError.badPayload("Unknown press \(name).") }
        let repeatCount = min(max(Int(RunnerQuery.string(payload["repeat"])) ?? 1, 1), 20)
        for _ in 0..<repeatCount {
            XCUIRemote.shared.press(button)
        }
        let focused = try RunnerElements.collect(app, limit: 2_000).first(where: \.focused)
        return ["pressed": name, "repeat": repeatCount, "focused": focused?.dictionary ?? NSNull()]
        #else
        throw RunnerCommandError.unsupported("ui.remote")
        #endif
    }
}
