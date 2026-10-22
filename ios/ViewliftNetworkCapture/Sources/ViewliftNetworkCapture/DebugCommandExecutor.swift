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
        guard let required = Self.module(for: command.name),
              config.debugModules.contains(required) else {
            throw DebugBridgeError.moduleDisabled
        }
        let prefix = command.name.split(separator: ".").first.map(String.init) ?? ""
        switch prefix {
        case "performance", "memory":
            return try memoryOrPerformance(command)
        case "ui", "swiftui", "assets", "accessibility", "bug":
            return try await visual(command)
        case "storage":
            return try storage(command)
        case "feature_flags":
            return try flags(command)
        case "runtime":
            return runtime(command)
        case "macro", "push":
            return try await qa(command)
        case "ott":
            return ott(command)
        case "crash", "hang":
            return crashHang(command)
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func memoryOrPerformance(
        _ command: DebugCommand
    ) throws -> [String: JSONValue] {
        switch command.name {
        case "performance.snapshot":
            return performance.sample()
        case "memory.snapshot":
            return MemoryDebugService.shared.snapshot()
        case "memory.graph":
            return MemoryDebugService.shared.graph()
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func ott(_ command: DebugCommand) -> [String: JSONValue] {
        switch command.name {
        case "ott.snapshot":
            return OTTDebugRegistry.snapshot()
        default:
            return ["error": .string("Unsupported ott command")]
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
        switch command.name {
        case "ui.hierarchy":
            return await UIInspectionService.hierarchy()
        case "ui.highlight":
            return await UIInspectionService.highlight(id: string(command.payload["id"]))
        case "ui.edit":
            return await UIInspectionService.edit(
                id: string(command.payload["id"]),
                payload: command.payload
            )
        case "ui.screenshot":
            return try await screenshot(limit: 480_000)
        case "bug.capture":
            var result = try await screenshot(limit: 400_000)
            result["performance"] = .object(performance.sample())
            result["memory"] = .object(MemoryDebugService.shared.snapshot())
            result["app"] = .string(Bundle.main.bundleIdentifier ?? "")
            return result
        case "swiftui.renders":
            return SwiftUIRenderTracker.shared.snapshot()
        case "assets.audit":
            return UIInspectionService.assetAudit()
        case "accessibility.audit":
            return await UIInspectionService.accessibilityAudit()
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
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func flags(_ command: DebugCommand) throws -> [String: JSONValue] {
        switch command.name {
        case "feature_flags.list":
            return DebugFeatureFlags.shared.snapshot()
        case "feature_flags.set":
            try DebugFeatureFlags.shared.set(
                string(command.payload["key"]),
                enabled: bool(command.payload["enabled"])
            )
            return DebugFeatureFlags.shared.snapshot()
        case "feature_flags.reset":
            try DebugFeatureFlags.shared.set(string(command.payload["key"]), enabled: nil)
            return DebugFeatureFlags.shared.snapshot()
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func runtime(_ command: DebugCommand) -> [String: JSONValue] {
        switch command.name {
        case "runtime.search":
            return RuntimeBrowser.search(string(command.payload["query"]))
        case "runtime.describe":
            return RuntimeBrowser.describe(string(command.payload["class"]))
        default:
            return ["error": .string("Unsupported runtime command")]
        }
    }

    private func qa(_ command: DebugCommand) async throws -> [String: JSONValue] {
        switch command.name {
        case "macro.record_start":
            MacroRecorder.shared.start()
            return ["recording": .bool(true)]
        case "macro.record_stop":
            return MacroRecorder.shared.stop()
        case "macro.list":
            return MacroRecorder.shared.snapshot()
        case "macro.replay":
            return await MacroRecorder.shared.replay(command.payload)
        case "push.local":
            try await PushDebugService.schedule(payload: command.payload)
            return ["scheduled": .bool(true)]
        default:
            throw DebugBridgeError.unsupportedCommand
        }
    }

    private func screenshot(limit: Int) async throws -> [String: JSONValue] {
        guard let data = await UIInspectionService.screenshot(), data.count <= limit else {
            throw DebugBridgeError.payloadTooLarge
        }
        return [
            "mime": .string("image/png"),
            "base64": .string(data.base64EncodedString())
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
            "feature_flags": .featureFlags,
            "runtime": .runtime,
            "macro": .macro,
            "bug": .bug,
            "push": .push,
            "ott": .ott,
            "crash": .crash,
            "hang": .hang
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
#endif
