#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Routing for the object explorer, remote settings, and storage edit commands.
func explorerCommand(_ command: DebugCommand) async throws -> [String: JSONValue] {
    let payload = command.payload
    return try await MainActor.run {
        let service = ObjectExplorerService.shared
        switch command.name {
        case "explorer.roots": return service.roots()
        case "explorer.object": return service.explore(payload)
        case "explorer.instances": return service.instances(payload)
        case "explorer.set": return service.set(payload)
        case "explorer.call": return service.call(payload)
        default: throw DebugBridgeError.unsupportedCommand
        }
    }
}

func settingsCommand(_ command: DebugCommand) throws -> [String: JSONValue] {
    switch command.name {
    case "settings.get":
        return RemoteSettingsService.shared.snapshot()
    case "settings.set":
        return try RemoteSettingsService.shared.update(command.payload)
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}

func storageEditCommand(_ command: DebugCommand, state: StateDebugService) throws -> [String: JSONValue] {
    let text = { (key: String) -> String in
        if case let .string(value) = command.payload[key] { return value }
        return ""
    }
    switch command.name {
    case "storage.defaults_delete":
        return try state.deleteDefault(key: text("key"))
    case "storage.file_rename":
        return try state.renameFile(relativePath: text("path"), newName: text("name"))
    case "storage.file_delete":
        return try state.deleteFile(relativePath: text("path"))
    case "storage.cookies":
        return state.cookies()
    case "storage.cookie_delete":
        return state.deleteCookie(name: text("name"), domain: text("domain"), path: text("path"))
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}
#endif
