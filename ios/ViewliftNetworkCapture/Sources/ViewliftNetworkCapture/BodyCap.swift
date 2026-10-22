import Foundation

enum BodyCap {
    static func truncate(_ data: Data, maxBytes: Int) -> Data {
        guard maxBytes >= 0 else { return Data() }
        if data.count <= maxBytes {
            return data
        }
        return data.prefix(maxBytes)
    }

    static func base64Capped(_ data: Data, maxBytes: Int) -> String? {
        let capped = truncate(data, maxBytes: maxBytes)
        if capped.isEmpty {
            return nil
        }
        return capped.base64EncodedString()
    }
}
