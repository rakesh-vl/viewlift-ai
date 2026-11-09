#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Host-app seam for deep OTT state (user, plan, TVE, CMS modules).
/// ViewliftNetworkCapture stays free of AppCMS types.
///
/// Host apps that define their own `JSONValue` (e.g. SwiftyJSON alias) must
/// qualify this return as `ViewliftNetworkCapture.JSONValue`.
public protocol OTTDebugSnapshotProviding: AnyObject {
    func ottDebugSnapshot() -> [String: JSONValue]
}

/// Optional richer host seam: named sections, diagnostics and confirmed actions.
/// Served by `ott.sections`, `ott.section`, `ott.diagnostics` and `ott.action`.
/// Section and diagnostics calls are async so the host can read main-actor state without blocking.
public protocol OTTDebugCommandHandling: AnyObject {
    var ottDebugProviderName: String { get }
    func ottDebugSections() -> [String]
    func ottDebugActions() -> [String]
    func ottDebugSection(_ name: String, arguments: [String: JSONValue]) async -> [String: JSONValue]?
    func ottDebugDiagnostics() async -> [JSONValue]
    func ottDebugPerform(_ action: String, payload: [String: JSONValue]) async throws -> [String: JSONValue]
    /// Optional dashboard menu: `title`, `pages` ([{name, title, group}]) and `action_forms` ({action: [fields]}).
    func ottDebugManifest() -> [String: JSONValue]
}

public extension OTTDebugCommandHandling {
    var ottDebugProviderName: String { "host" }
    func ottDebugManifest() -> [String: JSONValue] { [:] }
}

public enum OTTDebugError: Error, Equatable {
    case noHandler
    case unknownSection(String)
    case unknownAction(String)
    case actionFailed(String)
}

/// Registers the host provider used by `ott.snapshot` and the optional command handler.
public enum OTTDebugRegistry {
    private static let lock = NSLock()
    private static weak var provider: OTTDebugSnapshotProviding?
    private static weak var handler: OTTDebugCommandHandling?
    private static var changed: [String: Date] = [:]
    private static let changeDebounce: TimeInterval = 0.5
    private static var lastDrain = Date.distantPast

    public static func register(_ provider: OTTDebugSnapshotProviding) {
        lock.lock()
        self.provider = provider
        if let commands = provider as? OTTDebugCommandHandling {
            handler = commands
        }
        lock.unlock()
    }

    public static func register(handler: OTTDebugCommandHandling) {
        lock.lock()
        self.handler = handler
        lock.unlock()
    }

    public static func clear() {
        lock.lock()
        provider = nil
        handler = nil
        changed = [:]
        lastDrain = .distantPast
        lock.unlock()
    }

    /// Marks a section as changed. The bridge pushes one `ott/changed` event per debounce window.
    public static func notifyChanged(section: String) {
        lock.lock()
        changed[section] = Date()
        lock.unlock()
    }

    static func drainChanged(now: Date = Date()) -> [String]? {
        lock.lock()
        defer { lock.unlock() }
        guard !changed.isEmpty, now.timeIntervalSince(lastDrain) >= changeDebounce else { return nil }
        let names = changed.keys.sorted()
        changed = [:]
        lastDrain = now
        return names
    }

    static func activeHandler() -> OTTDebugCommandHandling? {
        lock.lock()
        defer { lock.unlock() }
        return handler
    }

    /// The provider snapshot, or every handler section merged when only a handler is registered.
    static func snapshot() async -> [String: JSONValue] {
        lock.lock()
        let active = provider
        let commands = handler
        lock.unlock()
        var payload = active?.ottDebugSnapshot() ?? [:]
        if active == nil, let commands {
            for name in commands.ottDebugSections() {
                if let body = await commands.ottDebugSection(name, arguments: [:]) {
                    payload[name] = .object(body)
                }
            }
            payload["provider"] = .string(commands.ottDebugProviderName)
        }
        if payload["provider"] == nil {
            payload["provider"] = .string(active == nil ? "none" : "host")
        }
        payload["captured_at"] = .number(Date().timeIntervalSince1970)
        payload["bundle"] = .object(Self.bundleInfo())
        payload["frameworks"] = .array(Self.frameworkNames())
        return payload
    }

    static func sections() -> [String: JSONValue] {
        guard let handler = activeHandler() else {
            return ["provider": .string("none"), "sections": .array([]), "actions": .array([])]
        }
        var payload: [String: JSONValue] = [
            "provider": .string(handler.ottDebugProviderName),
            "sections": .array(handler.ottDebugSections().map(JSONValue.string)),
            "actions": .array(handler.ottDebugActions().map(JSONValue.string)),
            "bundle": .object(Self.bundleInfo())
        ]
        for (key, value) in handler.ottDebugManifest() where payload[key] == nil {
            payload[key] = value
        }
        return payload
    }

    static func section(_ name: String, arguments: [String: JSONValue] = [:]) async throws -> [String: JSONValue] {
        guard let handler = activeHandler() else { throw OTTDebugError.noHandler }
        guard let body = await handler.ottDebugSection(name, arguments: arguments) else {
            throw OTTDebugError.unknownSection(name)
        }
        return [
            "section": .string(name),
            "captured_at": .number(Date().timeIntervalSince1970),
            "data": .object(body)
        ]
    }

    static func diagnostics() async throws -> [String: JSONValue] {
        guard let handler = activeHandler() else { throw OTTDebugError.noHandler }
        let findings = await handler.ottDebugDiagnostics()
        return [
            "captured_at": .number(Date().timeIntervalSince1970),
            "count": .number(Double(findings.count)),
            "findings": .array(findings)
        ]
    }

    static func perform(_ action: String, payload: [String: JSONValue]) async throws -> [String: JSONValue] {
        guard let handler = activeHandler() else { throw OTTDebugError.noHandler }
        guard handler.ottDebugActions().contains(action) else { throw OTTDebugError.unknownAction(action) }
        var result = try await handler.ottDebugPerform(action, payload: payload)
        result["action"] = .string(action)
        result["performed_at"] = .number(Date().timeIntervalSince1970)
        return result
    }

    private static func bundleInfo() -> [String: JSONValue] {
        let info = Bundle.main.infoDictionary ?? [:]
        return [
            "bundle_id": .string(Bundle.main.bundleIdentifier ?? ""),
            "display_name": .string(
                (info["CFBundleDisplayName"] as? String)
                    ?? (info["CFBundleName"] as? String)
                    ?? ""
            ),
            "version": .string(info["CFBundleShortVersionString"] as? String ?? ""),
            "build": .string(info["CFBundleVersion"] as? String ?? ""),
            "executable": .string(info["CFBundleExecutable"] as? String ?? "")
        ]
    }

    private static func frameworkNames() -> [JSONValue] {
        Bundle.allFrameworks
            .compactMap { $0.bundleIdentifier }
            .sorted()
            .prefix(300)
            .map(JSONValue.string)
    }
}
#endif
