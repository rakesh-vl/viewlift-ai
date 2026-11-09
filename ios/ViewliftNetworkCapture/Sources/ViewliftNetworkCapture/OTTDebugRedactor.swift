#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Turns host values into dashboard-safe JSON: tokens become metadata, URLs lose their query.
public enum OTTDebugRedactor {
    private static let secretFragments = [
        "token", "secret", "password", "passwd", "apikey", "api_key", "authorization", "cookie",
        "signature", "license", "skd", "credential", "statement", "privatekey", "private_key", "pin"
    ]
    private static let safeSecretKeys: Set<String> = ["pinenabled", "haspin", "pinset", "spinner"]
    private static let allowedClaims: Set<String> = ["exp", "iat", "nbf", "iss", "aud", "siteId", "site", "provider"]

    public static func isSecretKey(_ key: String) -> Bool {
        let lowered = key.lowercased()
        guard !safeSecretKeys.contains(lowered) else { return false }
        return secretFragments.contains { lowered.contains($0) }
    }

    /// Presence, length and the allowed JWT claims. The raw value is never returned.
    public static func token(_ raw: String?, now: Date = Date()) -> JSONValue {
        guard let raw, !raw.isEmpty else {
            return .object(["present": .bool(false)])
        }
        var body: [String: JSONValue] = [
            "present": .bool(true),
            "length": .number(Double(raw.count))
        ]
        if let claims = jwtClaims(raw) {
            body["jwt"] = .object(claims.filter { allowedClaims.contains($0.key) })
            if case let .number(exp) = claims["exp"] {
                let remaining = exp - now.timeIntervalSince1970
                body["expires_in_sec"] = .number(remaining.rounded())
                body["expired"] = .bool(remaining <= 0)
            }
        }
        return .object(body)
    }

    /// Decoded JWT payload, or nil when `raw` is not a JWT.
    public static func jwtClaims(_ raw: String) -> [String: JSONValue]? {
        let token = raw.hasPrefix("Bearer ") ? String(raw.dropFirst(7)) : raw
        let parts = token.split(separator: ".")
        guard parts.count == 3 else { return nil }
        var base64 = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        guard
            let data = Data(base64Encoded: base64),
            case let .object(claims)? = try? JSONDecoder().decode(JSONValue.self, from: data)
        else {
            return nil
        }
        return claims
    }

    /// `scheme://host/path` without query, fragment or user info.
    public static func url(_ raw: String?) -> JSONValue {
        guard let raw, !raw.isEmpty else { return .null }
        guard let components = URLComponents(string: raw), let host = components.host else {
            return .string("[unparsed]")
        }
        let scheme = components.scheme.map { "\($0)://" } ?? ""
        return .string(scheme + host + components.path)
    }

    public static func email(_ raw: String?) -> JSONValue {
        guard let raw, let at = raw.firstIndex(of: "@") else { return .null }
        let name = raw[..<at]
        return .string("\(name.prefix(1))***\(raw[at...])")
    }

    /// Recursively replaces secret-looking keys with token metadata and strips URL queries.
    public static func scrub(_ value: JSONValue) -> JSONValue {
        switch value {
        case let .object(object):
            var result: [String: JSONValue] = [:]
            for (key, item) in object {
                result[key] = scrubEntry(key: key, value: item)
            }
            return .object(result)
        case let .array(items):
            return .array(items.map(scrub))
        case let .string(text) where text.hasPrefix("http") && text.contains("?"):
            return url(text)
        default:
            return value
        }
    }

    private static func scrubEntry(key: String, value: JSONValue) -> JSONValue {
        guard isSecretKey(key) else { return scrub(value) }
        switch value {
        case let .string(text):
            return token(text)
        case .bool, .null:
            return value
        default:
            return .string("[redacted]")
        }
    }
}

/// Converts loosely typed host values (`userInfo`, `[String: Any]`, plist reads) to `JSONValue`.
public enum OTTDebugValue {
    public static func from(_ value: Any?, depth: Int = 0) -> JSONValue {
        guard let value, depth < 8 else { return .null }
        switch value {
        case let item as JSONValue:
            return item
        case let item as String:
            return .string(item)
        case let item as NSNumber:
            return number(item)
        case let item as Date:
            return .number(item.timeIntervalSince1970)
        case let item as URL:
            return .string(item.absoluteString)
        case let item as Data:
            return .string("<\(item.count) bytes>")
        case let item as [String: Any]:
            return .object(item.mapValues { from($0, depth: depth + 1) })
        case let item as [Any]:
            return .array(item.prefix(200).map { from($0, depth: depth + 1) })
        default:
            return .string(String(describing: type(of: value)))
        }
    }

    /// `from` followed by `OTTDebugRedactor.scrub`.
    public static func safe(_ value: Any?) -> JSONValue {
        OTTDebugRedactor.scrub(from(value))
    }

    private static func number(_ item: NSNumber) -> JSONValue {
        if CFGetTypeID(item) == CFBooleanGetTypeID() {
            return .bool(item.boolValue)
        }
        return .number(item.doubleValue)
    }
}

public extension JSONValue {
    subscript(key: String) -> JSONValue? {
        guard case let .object(object) = self else { return nil }
        return object[key]
    }

    var stringValue: String? {
        guard case let .string(value) = self else { return nil }
        return value
    }

    var boolValue: Bool? {
        guard case let .bool(value) = self else { return nil }
        return value
    }

    var doubleValue: Double? {
        guard case let .number(value) = self else { return nil }
        return value
    }

    var objectValue: [String: JSONValue]? {
        guard case let .object(value) = self else { return nil }
        return value
    }

    var arrayValue: [JSONValue]? {
        guard case let .array(value) = self else { return nil }
        return value
    }
}
#endif
