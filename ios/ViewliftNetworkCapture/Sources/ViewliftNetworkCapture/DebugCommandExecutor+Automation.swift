#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// UI automation (`ui.accessibility_tree`, `ui.tap`, ...) and preview rendering (`preview.*`).
extension DebugCommandExecutor {
    static let automationCommands: Set<String> = [
        "ui.accessibility_tree", "ui.find", "ui.wait_for", "ui.tap", "ui.long_press",
        "ui.swipe", "ui.scroll", "ui.type_text", "ui.key", "ui.remote"
    ]

    /// Nil when the command is not an automation command, so inspection commands still run.
    func automationCommand(_ command: DebugCommand) async -> [String: JSONValue]? {
        guard Self.automationCommands.contains(command.name) else { return nil }
        #if canImport(UIKit)
        let payload = AutomationPayload(command.payload)
        switch command.name {
        case "ui.accessibility_tree", "ui.find":
            return await AccessibilityTreeService.tree(payload)
        case "ui.wait_for":
            return await AccessibilityTreeService.waitFor(payload)
        case "ui.remote":
            return await TVRemoteService.press(payload)
        default:
            return await gesture(command.name, payload: payload)
        }
        #else
        return ["ok": .bool(false), "detail": .string("UI automation needs UIKit.")]
        #endif
    }

    func previewCommand(_ command: DebugCommand) async throws -> [String: JSONValue] {
        switch command.name {
        case "preview.list":
            return PreviewRegistry.shared.list()
        case "preview.render":
            #if canImport(UIKit)
            return try await PreviewRenderService.render(AutomationPayload(command.payload))
            #else
            return ["ok": .bool(false), "detail": .string("Preview rendering needs UIKit.")]
            #endif
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    #if canImport(UIKit)
    @MainActor
    private func gesture(_ name: String, payload: AutomationPayload) -> [String: JSONValue] {
        switch name {
        case "ui.tap":
            return UIAutomationService.tap(payload)
        case "ui.long_press":
            return UIAutomationService.longPress(payload)
        case "ui.swipe":
            return UIAutomationService.scroll(payload, fingerDirection: true)
        case "ui.scroll":
            return UIAutomationService.scroll(payload, fingerDirection: false)
        case "ui.type_text":
            return UIAutomationService.typeText(payload)
        default:
            return UIAutomationService.key(payload)
        }
    }
    #endif
}
#endif
