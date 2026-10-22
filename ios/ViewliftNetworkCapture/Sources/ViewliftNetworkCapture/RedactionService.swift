import Foundation

public final class HeaderJSONRedactor: Redacting, @unchecked Sendable {
    private let headerKeys: Set<String>
    private let jsonKeys: Set<String>
    private let replacement = "[REDACTED]"

    public init(extraKeys: [String] = []) {
        var headers: Set<String> = ["cookie", "set-cookie"]
        var json: Set<String> = ["password", "secret"]
        for key in extraKeys {
            let lower = key.lowercased()
            headers.insert(lower)
            json.insert(lower)
        }
        self.headerKeys = headers
        self.jsonKeys = json
    }

    public func redact(headers: [String: String]) -> [String: String] {
        var out: [String: String] = [:]
        out.reserveCapacity(headers.count)
        for (key, value) in headers {
            if headerKeys.contains(key.lowercased()) {
                out[key] = replacement
            } else {
                out[key] = value
            }
        }
        return out
    }

    public func redact(jsonData: Data) -> Data {
        guard !jsonData.isEmpty else { return jsonData }
        guard let object = try? JSONSerialization.jsonObject(with: jsonData) else {
            return jsonData
        }
        let redacted = redactValue(object)
        guard JSONSerialization.isValidJSONObject(redacted),
              let data = try? JSONSerialization.data(withJSONObject: redacted, options: []) else {
            return jsonData
        }
        return data
    }

    private func redactValue(_ value: Any) -> Any {
        if let dict = value as? [String: Any] {
            var out: [String: Any] = [:]
            for (key, nested) in dict {
                if jsonKeys.contains(key.lowercased()) {
                    out[key] = replacement
                } else {
                    out[key] = redactValue(nested)
                }
            }
            return out
        }
        if let array = value as? [Any] {
            return array.map { redactValue($0) }
        }
        return value
    }
}
