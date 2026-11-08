#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

enum NavigationURL {
    static func parse(_ raw: String) throws -> URL {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count <= 2_048,
              let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              isAllowed(scheme) else {
            throw DebugBridgeError.invalidPayload
        }
        return url
    }

    /// Scheme, host, and path only. Query, fragment, and userinfo can carry tokens.
    static func display(_ url: URL) -> String {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return url.scheme ?? ""
        }
        components.query = nil
        components.fragment = nil
        components.user = nil
        components.password = nil
        return components.string ?? url.scheme ?? ""
    }

    private static func isAllowed(_ scheme: String) -> Bool {
        let blocked: Set<String> = ["javascript", "data", "file", "about", "blob"]
        guard !blocked.contains(scheme), (1...64).contains(scheme.count) else { return false }
        return scheme.unicodeScalars.allSatisfy { scalar in
            CharacterSet.alphanumerics.contains(scalar)
                || scalar == "-"
                || scalar == "+"
                || scalar == "."
        }
    }
}

enum NavigationDebug {
    static func open(raw: String) async throws -> [String: JSONValue] {
        let url = try NavigationURL.parse(raw)
        #if canImport(UIKit)
        let opened = await openOnDevice(url)
        return [
            "opened": .bool(opened),
            "url": .string(NavigationURL.display(url))
        ]
        #else
        _ = url
        throw DebugBridgeError.unsupportedCommand
        #endif
    }

    #if canImport(UIKit)
    @MainActor
    private static func openOnDevice(_ url: URL) async -> Bool {
        await UIApplication.shared.open(url)
    }
    #endif
}
#endif
