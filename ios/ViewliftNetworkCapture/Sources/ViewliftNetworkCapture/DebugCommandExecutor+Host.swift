#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Commands answered by host-app integrations: OTT snapshot, activity, playback, navigation.
func hostCommand(_ command: DebugCommand) async throws -> [String: JSONValue] {
    switch command.name.split(separator: ".").first.map(String.init) ?? "" {
    case "playback":
        return try playbackCommand(command)
    case "nav":
        return try await navigationCommand(command)
    default:
        return try await ottOrActivity(command)
    }
}

private func ottOrActivity(_ command: DebugCommand) async throws -> [String: JSONValue] {
    guard command.name.hasPrefix("activity.") else {
        if command.name == "ott.snapshot" {
            return OTTDebugRegistry.snapshot()
        }
        return ["error": .string("Unsupported ott command")]
    }
    return try await executeActivity(command)
}

private func executeActivity(_ command: DebugCommand) async throws -> [String: JSONValue] {
    let service = ActivityDebugService.shared
    switch command.name {
    case "activity.snapshot":
        return await service.snapshot()
    case "activity.live_start":
        let preview = command.payload["preview"] == .bool(true)
        return await service.liveState(enabled: true, preview: preview)
    case "activity.live_stop":
        return await service.liveState(enabled: false)
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}

private func playbackCommand(_ command: DebugCommand) throws -> [String: JSONValue] {
    let service = PlaybackDebugService.shared
    switch command.name {
    case "playback.snapshot":
        return service.snapshot()
    case "playback.drm":
        return service.drmSnapshot()
    case "playback.waterfall":
        return service.waterfallSnapshot()
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}

private func navigationCommand(_ command: DebugCommand) async throws -> [String: JSONValue] {
    guard command.name == "nav.open_url" else {
        throw DebugBridgeError.unsupportedCommand
    }
    let raw: String
    if case let .string(value) = command.payload["url"] {
        raw = value
    } else {
        raw = ""
    }
    return try await NavigationDebug.open(raw: raw)
}
#endif
