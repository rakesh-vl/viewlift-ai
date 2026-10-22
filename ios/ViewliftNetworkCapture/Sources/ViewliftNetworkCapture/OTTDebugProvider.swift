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

/// Registers the host provider used by `ott.snapshot`.
public enum OTTDebugRegistry {
    private static let lock = NSLock()
    private static weak var provider: OTTDebugSnapshotProviding?

    public static func register(_ provider: OTTDebugSnapshotProviding) {
        lock.lock()
        self.provider = provider
        lock.unlock()
    }

    public static func clear() {
        lock.lock()
        provider = nil
        lock.unlock()
    }

    static func snapshot() -> [String: JSONValue] {
        lock.lock()
        let active = provider
        lock.unlock()
        var payload = active?.ottDebugSnapshot() ?? [:]
        if payload["provider"] == nil {
            payload["provider"] = .string(active == nil ? "none" : "host")
        }
        payload["captured_at"] = .number(Date().timeIntervalSince1970)
        payload["bundle"] = .object(Self.bundleInfo())
        payload["frameworks"] = .array(Self.frameworkNames())
        return payload
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
