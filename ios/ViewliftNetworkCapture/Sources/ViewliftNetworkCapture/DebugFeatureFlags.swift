#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

public struct DebugFeatureFlag: Sendable {
    public let key: String
    public let title: String
    public let detail: String
    public let group: String
    public let defaultValue: Bool

    public init(
        key: String,
        title: String,
        detail: String = "",
        group: String = "General",
        defaultValue: Bool
    ) {
        self.key = key
        self.title = title
        self.detail = detail
        self.group = group
        self.defaultValue = defaultValue
    }
}

public final class DebugFeatureFlags: @unchecked Sendable {
    public static let shared = DebugFeatureFlags()

    private let lock = NSLock()
    private var flags: [String: DebugFeatureFlag] = [:]
    private var overrides: [String: Bool] = [:]

    private init() {}

    public func register(_ flag: DebugFeatureFlag) {
        lock.lock()
        flags[flag.key] = flag
        lock.unlock()
    }

    public func isEnabled(_ key: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return overrides[key] ?? flags[key]?.defaultValue ?? false
    }

    func set(_ key: String, enabled: Bool?) throws {
        lock.lock()
        defer { lock.unlock() }
        guard flags[key] != nil else { throw FeatureFlagError.notRegistered }
        overrides[key] = enabled
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let rows = flags.values.map { flag -> JSONValue in
            .object([
                "key": .string(flag.key),
                "title": .string(flag.title),
                "detail": .string(flag.detail),
                "group": .string(flag.group),
                "default": .bool(flag.defaultValue),
                "enabled": .bool(overrides[flag.key] ?? flag.defaultValue),
                "overridden": .bool(overrides[flag.key] != nil)
            ])
        }
        lock.unlock()
        return ["flags": .array(rows)]
    }
}

enum FeatureFlagError: Error {
    case notRegistered
}
#endif
