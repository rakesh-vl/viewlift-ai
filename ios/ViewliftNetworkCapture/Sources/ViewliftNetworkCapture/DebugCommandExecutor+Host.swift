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
        return try await ottCommand(command)
    }
    return try await executeActivity(command)
}

func ottCommand(_ command: DebugCommand) async throws -> [String: JSONValue] {
    switch command.name {
    case "ott.snapshot":
        return await OTTDebugRegistry.snapshot()
    case "ott.sections":
        return OTTDebugRegistry.sections()
    case "ott.section":
        guard case let .string(name) = command.payload["name"], !name.isEmpty else {
            throw DebugBridgeError.invalidPayload
        }
        var arguments: [String: JSONValue] = [:]
        if case let .object(value) = command.payload["args"] {
            arguments = value
        }
        return try await ottResult { try await OTTDebugRegistry.section(name, arguments: arguments) }
    case "ott.diagnostics":
        return try await ottResult { try await OTTDebugRegistry.diagnostics() }
    case "ott.action":
        guard case let .string(name) = command.payload["name"], !name.isEmpty else {
            throw DebugBridgeError.invalidPayload
        }
        var arguments: [String: JSONValue] = [:]
        if case let .object(value) = command.payload["payload"] {
            arguments = value
        }
        do {
            return try await OTTDebugRegistry.perform(name, payload: arguments)
        } catch let error as OTTDebugError {
            return ottErrorPayload(error)
        }
    default:
        throw DebugBridgeError.unsupportedCommand
    }
}

private func ottResult(_ body: () async throws -> [String: JSONValue]) async throws -> [String: JSONValue] {
    do {
        return try await body()
    } catch let error as OTTDebugError {
        return ottErrorPayload(error)
    }
}

private func ottErrorPayload(_ error: OTTDebugError) -> [String: JSONValue] {
    switch error {
    case .noHandler:
        return ["error": .string("no_handler"), "detail": .string("Host app registered no OTTDebugCommandHandling")]
    case let .unknownSection(name):
        return ["error": .string("unknown_section"), "detail": .string(name)]
    case let .unknownAction(name):
        return ["error": .string("unknown_action"), "detail": .string(name)]
    case let .actionFailed(reason):
        return ["error": .string("action_failed"), "detail": .string(reason)]
    }
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
