#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

final class DebugCommandExecutor {
    private let performance: PerformanceCollector
    private let state = StateDebugService()

    init(performance: PerformanceCollector) {
        self.performance = performance
    }

    func execute(
        _ command: DebugCommand,
        config: CaptureConfiguration
    ) async throws -> [String: JSONValue] {
        // Settings stay reachable with every module off, so the dashboard can turn modules back on.
        if command.name.hasPrefix("settings.") {
            return try settingsCommand(command)
        }
        if command.name == "net.profile" {
            return try NetworkShaper.shared.apply(command.payload)
        }
        return try await executeModule(command, config: config)
    }

    private func executeModule(
        _ command: DebugCommand,
        config: CaptureConfiguration
    ) async throws -> [String: JSONValue] {
        guard let required = Self.module(for: command.name),
              config.debugModules.contains(required) else {
            throw DebugBridgeError.moduleDisabled
        }
        let prefix = command.name.split(separator: ".").first.map(String.init) ?? ""
        switch prefix {
        case "performance", "memory":
            return try await memoryOrPerformance(command)
        case "ui", "swiftui", "assets", "accessibility", "bug", "preview":
            return try await visual(command)
        case "storage":
            return try storage(command)
        case "runtime", "explorer":
            return try await introspection(command)
        case "macro", "push":
            return try await qa(command)
        case "ott", "activity", "playback", "nav":
            return try await hostCommand(command)
        case "crash", "hang":
            return crashHang(command)
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func memoryOrPerformance(
        _ command: DebugCommand
    ) async throws -> [String: JSONValue] {
        let memory = MemoryDebugService.shared
        switch command.name {
        case "performance.snapshot":
            return performance.sample()
        case "memory.snapshot":
            return memory.snapshot()
        case "memory.graph":
            return await memory.graph()
        case "memory.leaks":
            return memory.leakList()
        case "memory.heap":
            return memory.heap()
        case "memory.object":
            return await memory.objectDetail(id: string(command.payload["id"]))
        case "memory.live_start":
            return memory.liveState(enabled: true)
        case "memory.live_stop":
            return memory.liveState(enabled: false)
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func crashHang(_ command: DebugCommand) -> [String: JSONValue] {
        switch command.name {
        case "hang.list":
            return CrashHangRecorder.shared.hangList()
        case "crash.list":
            return CrashHangRecorder.shared.crashList()
        default:
            return ["error": .string("Unsupported crash/hang command")]
        }
    }

    private func visual(_ command: DebugCommand) async throws -> [String: JSONValue] {
        if command.name.hasPrefix("ui.") {
            if let automation = await automationCommand(command) {
                return automation
            }
            return try await uiInspectionCommand(command, capture: visualCapture)
        }
        if command.name.hasPrefix("preview.") {
            return try await previewCommand(command)
        }
        switch command.name {
        case "bug.capture":
            return try await BugReportService.capture(performance: performance.sample())
        case "swiftui.renders":
            return SwiftUIRenderTracker.shared.snapshot()
        case "assets.audit":
            return UIInspectionService.assetAudit()
        case "accessibility.audit":
            return await AccessibilityAuditService.audit()
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func storage(_ command: DebugCommand) throws -> [String: JSONValue] {
        switch command.name {
        case "storage.defaults":
            return state.defaults()
        case "storage.defaults_set":
            return try state.setDefault(
                key: string(command.payload["key"]),
                value: command.payload["value"]
            )
        case "storage.keychain_metadata":
            return state.keychainMetadata()
        case "storage.files":
            return try state.listFiles(relativePath: string(command.payload["path"]))
        case "storage.file_read":
            return try state.readFile(relativePath: string(command.payload["path"]))
        case "storage.file_write":
            try state.writeFile(
                relativePath: string(command.payload["path"]),
                content: string(command.payload["content"]),
                base64: bool(command.payload["base64"])
            )
            return ["updated": .bool(true)]
        default:
            return try storageAdvanced(command)
        }
    }

    private func storageAdvanced(_ command: DebugCommand) throws -> [String: JSONValue] {
        switch command.name {
        case "storage.sqlite_schema":
            return try state.sqliteSchema(relativePath: string(command.payload["path"]))
        case "storage.sqlite_query":
            return try state.sqlite(
                relativePath: string(command.payload["path"]),
                query: string(command.payload["query"])
            )
        case "storage.snapshot_create":
            return try state.snapshot(paths: strings(command.payload["paths"]))
        case "storage.snapshot_restore":
            guard case let .object(snapshot) = command.payload["snapshot"] else {
                throw DebugBridgeError.invalidPayload
            }
            try state.restore(snapshot: snapshot)
            return ["restored": .bool(true)]
        default:
            return try storageEditCommand(command, state: state)
        }
    }

}

/// Runtime, QA, capture helpers, and payload decoding.
extension DebugCommandExecutor {
    private func introspection(_ command: DebugCommand) async throws -> [String: JSONValue] {
        if command.name.hasPrefix("explorer.") {
            return try await explorerCommand(command)
        }
        return runtime(command)
    }

    private func runtime(_ command: DebugCommand) -> [String: JSONValue] {
        switch command.name {
        case "runtime.search":
            return RuntimeBrowser.search(string(command.payload["query"]), appOnly: bool(command.payload["app_only"]))
        case "runtime.describe":
            return RuntimeBrowser.describe(string(command.payload["class"]))
        case "runtime.libraries":
            return RuntimeBrowser.libraries()
        case "runtime.image_classes":
            return RuntimeBrowser.classes(inImage: string(command.payload["image"]))
        default:
            return ["error": .string("Unsupported runtime command")]
        }
    }

    private func qa(_ command: DebugCommand) async throws -> [String: JSONValue] {
        switch command.name {
        case "macro.record_start":
            return MacroRecorder.shared.start()
        case "macro.record_stop":
            return MacroRecorder.shared.stop()
        case "macro.list":
            return MacroRecorder.shared.snapshot()
        case "macro.replay":
            return await MacroRecorder.shared.replay(command.payload)
        case "push.local":
            return try await PushDebugService.schedule(payload: command.payload)
        case "push.status":
            return await PushDebugService.status()
        case "push.clear":
            return await PushDebugService.clear()
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func visualCapture(_ command: DebugCommand) async throws -> [String: JSONValue] {
        if command.name == "ui.focus" {
            return await UIInspectionService.focus()
        }
        if command.payload["preview"] == .bool(true) {
            guard let payload = await PreviewCapture.jpeg() else {
                throw DebugBridgeError.payloadTooLarge
            }
            return payload
        }
        return try await screenshot(AutomationPayload(command.payload), limit: 480_000)
    }

    private func screenshot(_ payload: AutomationPayload, limit: Int) async throws -> [String: JSONValue] {
        let shot = await UIInspectionService.screenshot(
            windowId: payload.string("window_id"),
            sceneId: payload.string("scene_id")
        )
        guard let shot, shot.data.count <= limit else {
            throw DebugBridgeError.payloadTooLarge
        }
        return [
            "mime": .string("image/png"),
            "base64": .string(shot.data.base64EncodedString()),
            "width": .number(shot.width),
            "height": .number(shot.height),
            "window_id": .string(shot.windowId),
            "scene_id": .string(shot.sceneId)
        ]
    }

    private static func module(for command: String) -> DebugModule? {
        let prefix = command.split(separator: ".").first.map(String.init) ?? ""
        let mapping: [String: DebugModule] = [
            "performance": .performance,
            "memory": .memory,
            "ui": .ui,
            "swiftui": .swiftUI,
            "assets": .assets,
            "accessibility": .accessibility,
            "storage": .storage,
            "runtime": .runtime,
            "macro": .macro,
            "bug": .bug,
            "push": .push,
            "ott": .ott,
            "crash": .crash,
            "hang": .hang,
            "activity": .activity,
            "playback": .playback,
            "nav": .navigation,
            "explorer": .explorer,
            "preview": .preview
        ]
        return mapping[prefix]
    }

    private func string(_ value: JSONValue?) -> String {
        if case let .string(item) = value { return item }
        return ""
    }

    private func bool(_ value: JSONValue?) -> Bool {
        if case let .bool(item) = value { return item }
        return false
    }

    private func strings(_ value: JSONValue?) -> [String] {
        guard case let .array(items) = value else { return [] }
        return items.compactMap {
            if case let .string(item) = $0 { return item }
            return nil
        }
    }
}

enum DebugBridgeError: Error {
    case moduleDisabled
    case invalidPayload
    case payloadTooLarge
    case unsupportedCommand
}

private func uiInspectionCommand(
    _ command: DebugCommand,
    capture: (DebugCommand) async throws -> [String: JSONValue]
) async throws -> [String: JSONValue] {
    let identifier = commandIdentifier(command.payload["id"])
    switch command.name {
    case "ui.hierarchy":
        return await UIInspectionService.hierarchy()
    case "ui.describe":
        return await UIInspectionService.describe(id: identifier)
    case "ui.layers":
        return await UIInspectionService.layers(command.payload)
    case "ui.highlight":
        return await UIInspectionService.highlight(id: identifier)
    case "ui.visual":
        return await UIInspectionService.visual(command.payload)
    case "ui.edit":
        return await UIInspectionService.edit(id: identifier, payload: command.payload)
    case "ui.screenshot", "ui.focus":
        return try await capture(command)
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}

private func commandIdentifier(_ value: JSONValue?) -> String {
    if case let .string(item) = value { return item }
    return ""
}
#endif
