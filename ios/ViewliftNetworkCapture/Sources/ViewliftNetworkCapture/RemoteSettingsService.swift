#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Dashboard overrides layered over the host's `CaptureConfiguration`. Nil fields keep the host value.
struct RemoteCaptureSettings: Codable, Equatable {
    var debugModules: [String]?
    var captureNetwork: Bool?
    var maxRequestBodyBytes: Int?
    var maxResponseBodyBytes: Int?
    var bodyContentTypes: [String]?
}

protocol RemoteSettingsStoring {
    func load() -> RemoteCaptureSettings
    func save(_ settings: RemoteCaptureSettings)
}

struct UserDefaultsRemoteSettingsStore: RemoteSettingsStoring {
    static let suiteName = "viewlift.capture.settings"
    private static let key = "overrides"
    private let defaults: UserDefaults

    init(defaults: UserDefaults? = UserDefaults(suiteName: Self.suiteName)) {
        self.defaults = defaults ?? .standard
    }

    func load() -> RemoteCaptureSettings {
        guard let data = defaults.data(forKey: Self.key),
              let value = try? JSONDecoder().decode(RemoteCaptureSettings.self, from: data) else {
            return RemoteCaptureSettings()
        }
        return value
    }

    func save(_ settings: RemoteCaptureSettings) {
        if settings == RemoteCaptureSettings() {
            defaults.removeObject(forKey: Self.key)
        } else if let data = try? JSONEncoder().encode(settings) {
            defaults.set(data, forKey: Self.key)
        }
    }
}

/// Lets the dashboard turn debug modules and capture options on or off. Overrides persist
/// across launches and apply live through `onChange`.
final class RemoteSettingsService: @unchecked Sendable {
    static let shared = RemoteSettingsService()
    static let maxBodyBytes = 262_144

    private let store: RemoteSettingsStoring
    private let lock = NSLock()
    private var settings: RemoteCaptureSettings
    private var base: CaptureConfiguration?
    private var changeHandler: ((CaptureConfiguration) -> Void)?

    init(store: RemoteSettingsStoring = UserDefaultsRemoteSettingsStore()) {
        self.store = store
        settings = store.load()
    }

    var current: RemoteCaptureSettings {
        lock.lock()
        defer { lock.unlock() }
        return settings
    }

    /// Records the host configuration and returns it with overrides applied.
    func bind(base configuration: CaptureConfiguration, onChange: ((CaptureConfiguration) -> Void)? = nil)
        -> CaptureConfiguration {
        lock.lock()
        base = configuration
        if let onChange { changeHandler = onChange }
        let value = settings
        lock.unlock()
        return Self.apply(value, to: configuration)
    }

    static func apply(
        _ settings: RemoteCaptureSettings,
        to configuration: CaptureConfiguration
    ) -> CaptureConfiguration {
        var out = configuration
        if let modules = settings.debugModules {
            out.debugModules = Set(modules.compactMap(DebugModule.init(rawValue:)))
                .intersection(configuration.debugModules)
        }
        if let flag = settings.captureNetwork { out.captureNetwork = flag }
        if let bytes = settings.maxRequestBodyBytes { out.maxRequestBodyBytes = bytes }
        if let bytes = settings.maxResponseBodyBytes { out.maxResponseBodyBytes = bytes }
        if let types = settings.bodyContentTypes { out.bodyContentTypes = types }
        return out
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let value = settings
        let host = base
        lock.unlock()
        let effective = host.map { Self.apply(value, to: $0) }
        let modules = DebugModule.allCases.map { module in
            JSONValue.object([
                "id": .string(module.rawValue),
                "enabled": .bool(effective?.debugModules.contains(module) ?? false),
                "host_enabled": .bool(host?.debugModules.contains(module) ?? false)
            ])
        }
        return [
            "modules": .array(modules),
            "capture_network": .bool(effective?.captureNetwork ?? true),
            "max_request_body_bytes": .number(Double(effective?.maxRequestBodyBytes ?? 0)),
            "max_response_body_bytes": .number(Double(effective?.maxResponseBodyBytes ?? 0)),
            "body_content_types": .array((effective?.bodyContentTypes ?? []).map(JSONValue.string)),
            "max_body_bytes_limit": .number(Double(Self.maxBodyBytes)),
            "overridden": .bool(value != RemoteCaptureSettings())
        ]
    }

    /// Payload keys: `modules`, `capture_network`, `max_request_body_bytes`, `max_response_body_bytes`,
    /// `body_content_types`, and `reset` (true clears every override). JSON null clears one override.
    func update(_ payload: [String: JSONValue]) throws -> [String: JSONValue] {
        lock.lock()
        var next = settings
        lock.unlock()
        if case .bool(true) = payload["reset"] {
            next = RemoteCaptureSettings()
        }
        if let value = payload["modules"] {
            next.debugModules = try Self.strings(value, allowed: Set(DebugModule.allCases.map(\.rawValue)))
        }
        if let value = payload["capture_network"] {
            next.captureNetwork = try Self.bool(value)
        }
        if let value = payload["max_request_body_bytes"] {
            next.maxRequestBodyBytes = try Self.bytes(value)
        }
        if let value = payload["max_response_body_bytes"] {
            next.maxResponseBodyBytes = try Self.bytes(value)
        }
        if let value = payload["body_content_types"] {
            next.bodyContentTypes = try Self.strings(value, allowed: nil)
        }
        lock.lock()
        settings = next
        let host = base
        let handler = changeHandler
        lock.unlock()
        store.save(next)
        if let host {
            handler?(Self.apply(next, to: host))
        }
        return snapshot()
    }

    private static func strings(_ value: JSONValue, allowed: Set<String>?) throws -> [String]? {
        if case .null = value { return nil }
        guard case let .array(items) = value else { throw DebugBridgeError.invalidPayload }
        let out = try items.map { item -> String in
            guard case let .string(text) = item else { throw DebugBridgeError.invalidPayload }
            let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if let allowed, !allowed.contains(clean) { throw DebugBridgeError.invalidPayload }
            return clean
        }
        return Array(Set(out.filter { !$0.isEmpty })).sorted()
    }

    private static func bool(_ value: JSONValue) throws -> Bool? {
        switch value {
        case .null: return nil
        case let .bool(flag): return flag
        default: throw DebugBridgeError.invalidPayload
        }
    }

    private static func bytes(_ value: JSONValue) throws -> Int? {
        switch value {
        case .null: return nil
        case let .number(number) where number.isFinite: return max(0, min(maxBodyBytes, Int(safe: number)))
        default: throw DebugBridgeError.invalidPayload
        }
    }
}
#endif
