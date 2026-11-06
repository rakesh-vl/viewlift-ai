#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Reads the host app's identity from its bundles so the Mac dashboard never needs app-specific constants.
struct AppIdentity: Sendable {
    let appName: String
    let bundleId: String
    let appVersion: String
    let build: String
    let executable: String
    let urlSchemes: [String]
    let frameworks: [DebugFramework]

    static let maxFrameworks = 400

    static func current(bundle: Bundle = .main, frameworks: [Bundle] = Bundle.allFrameworks) -> AppIdentity {
        let info = bundle.infoDictionary ?? [:]
        return AppIdentity(
            appName: string(info, "CFBundleDisplayName") ?? string(info, "CFBundleName") ?? "Unknown",
            bundleId: bundle.bundleIdentifier ?? "",
            appVersion: string(info, "CFBundleShortVersionString") ?? "",
            build: string(info, "CFBundleVersion") ?? "",
            executable: string(info, "CFBundleExecutable") ?? "",
            urlSchemes: urlSchemes(info),
            frameworks: thirdPartyFrameworks(frameworks)
        )
    }

    private static func string(_ info: [String: Any], _ key: String) -> String? {
        guard let value = info[key] as? String, !value.isEmpty else { return nil }
        return value
    }

    private static func urlSchemes(_ info: [String: Any]) -> [String] {
        let types = info["CFBundleURLTypes"] as? [[String: Any]] ?? []
        var seen = Set<String>()
        return types
            .flatMap { $0["CFBundleURLSchemes"] as? [String] ?? [] }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    private static func thirdPartyFrameworks(_ bundles: [Bundle]) -> [DebugFramework] {
        let rows: [DebugFramework] = bundles.compactMap { bundle in
            guard let identifier = bundle.bundleIdentifier,
                  !identifier.hasPrefix("com.apple."),
                  !bundle.bundlePath.hasPrefix("/System/") else { return nil }
            let info = bundle.infoDictionary ?? [:]
            return DebugFramework(
                id: identifier,
                version: string(info, "CFBundleShortVersionString") ?? string(info, "CFBundleVersion") ?? "",
                name: bundle.bundleURL.deletingPathExtension().lastPathComponent
            )
        }
        return Array(rows.sorted { $0.id.lowercased() < $1.id.lowercased() }.prefix(maxFrameworks))
    }
}
#endif
