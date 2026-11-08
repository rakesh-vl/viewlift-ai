import XCTest

/// Touch gestures and hardware buttons. Apple TV has no touch screen, so it gets `ui.remote` only.
extension RunnerCommands {
    func gesture(name: String, payload: [String: Any]) throws -> [String: Any] {
        #if os(iOS)
        switch name {
        case "ui.tap":
            return try tap(payload, hold: 0)
        case "ui.long_press":
            return try tap(payload, hold: max(Double(RunnerQuery.string(payload["duration"])) ?? 1, 0.5))
        case "ui.swipe", "ui.scroll":
            return try swipe(payload, finger: name == "ui.swipe")
        case "ui.drag":
            return try drag(payload)
        case "ui.touch":
            return try touch(payload)
        case "ui.key":
            return try key(payload)
        case "ui.button":
            return try button(payload)
        default:
            throw RunnerCommandError.unsupported(name)
        }
        #else
        throw RunnerCommandError.unsupported(name)
        #endif
    }

    func focus(_ query: RunnerQuery) throws {
        #if os(iOS)
        _ = try tap(["label": query.label, "identifier": query.identifier, "text": query.text], hold: 0)
        #else
        throw RunnerCommandError.unsupported("focusing a field by query")
        #endif
    }

    #if os(iOS)
    private func point(_ x: Double, _ y: Double) -> XCUICoordinate {
        app.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: x, dy: y))
    }

    private func number(_ payload: [String: Any], _ key: String) -> Double? {
        Double(RunnerQuery.string(payload[key]))
    }

    private func target(_ payload: [String: Any]) throws -> (XCUICoordinate, [String: Any]?) {
        if let x = number(payload, "x"), let y = number(payload, "y"), RunnerQuery(payload).isEmpty {
            return (point(x, y), nil)
        }
        let index = Int(RunnerQuery.string(payload["index"])) ?? 0
        guard let element = try RunnerElements.first(app, query: RunnerQuery(payload), index: index) else {
            throw RunnerCommandError.notFound
        }
        return (point(Double(element.frame.midX), Double(element.frame.midY)), element.dictionary)
    }

    private func tap(_ payload: [String: Any], hold: Double) throws -> [String: Any] {
        let (coordinate, element) = try target(payload)
        if hold > 0 {
            coordinate.press(forDuration: min(hold, 10))
        } else {
            coordinate.tap()
        }
        return ["tapped": true, "element": element ?? NSNull()]
    }

    private func swipe(_ payload: [String: Any], finger: Bool) throws -> [String: Any] {
        let direction = RunnerQuery.string(payload["direction"]).lowercased()
        let vectors: [String: CGVector] = [
            "up": CGVector(dx: 0, dy: -1), "down": CGVector(dx: 0, dy: 1),
            "left": CGVector(dx: -1, dy: 0), "right": CGVector(dx: 1, dy: 0)
        ]
        guard var vector = vectors[direction] else {
            throw RunnerCommandError.badPayload("Pass direction up, down, left, or right.")
        }
        if !finger {
            vector = CGVector(dx: -vector.dx, dy: -vector.dy)
        }
        let frame = app.frame
        let center = point(Double(frame.midX), Double(frame.midY))
        let distance = Double(min(frame.width, frame.height)) * 0.35
        center.press(
            forDuration: 0.05,
            thenDragTo: center.withOffset(CGVector(dx: vector.dx * distance, dy: vector.dy * distance))
        )
        return ["swiped": direction]
    }

    private func drag(_ payload: [String: Any]) throws -> [String: Any] {
        guard let startX = number(payload, "startX"), let startY = number(payload, "startY"),
              let endX = number(payload, "endX"), let endY = number(payload, "endY") else {
            throw RunnerCommandError.badPayload("Pass startX, startY, endX, and endY.")
        }
        let hold = min(max(number(payload, "duration") ?? 1, 0.1), 10)
        point(startX, startY).press(forDuration: hold, thenDragTo: point(endX, endY))
        return ["dragged": true]
    }

    private func touch(_ payload: [String: Any]) throws -> [String: Any] {
        guard let x = number(payload, "x"), let y = number(payload, "y") else {
            throw RunnerCommandError.badPayload("Pass x and y.")
        }
        point(x, y).tap()
        return ["touched": true]
    }

    private func key(_ payload: [String: Any]) throws -> [String: Any] {
        let keys: [String: String] = [
            "return": "\n", "delete": XCUIKeyboardKey.delete.rawValue,
            "tab": "\t", "escape": XCUIKeyboardKey.escape.rawValue
        ]
        let name = RunnerQuery.string(payload["key"]).lowercased()
        guard let text = keys[name] else { throw RunnerCommandError.badPayload("Unknown key \(name).") }
        app.typeText(text)
        return ["key": name]
    }

    private func button(_ payload: [String: Any]) throws -> [String: Any] {
        let name = RunnerQuery.string(payload["button"]).lowercased()
        switch name {
        case "home":
            XCUIDevice.shared.press(.home)
        #if !targetEnvironment(simulator)
        case "volume-up", "volumeup":
            XCUIDevice.shared.press(.volumeUp)
        case "volume-down", "volumedown":
            XCUIDevice.shared.press(.volumeDown)
        #endif
        default:
            throw RunnerCommandError.badPayload("Buttons: home, volume-up, volume-down.")
        }
        return ["pressed": name]
    }
    #endif
}
