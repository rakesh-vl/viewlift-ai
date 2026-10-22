import Foundation

public enum DebugCapability: String, Codable, Sendable {
    case available
    case partial
    case unsupported
}

public enum DebugModule: String, CaseIterable, Hashable, Sendable {
    case performance
    case memory
    case ui
    case swiftUI = "swiftui"
    case assets
    case accessibility
    case storage
    case featureFlags = "feature_flags"
    case runtime
    case macro
    case bug
    case push
    case ott
    case crash
    case hang
}

public enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() {
            self = .null
        } else if let decoded = try? value.decode(Bool.self) {
            self = .bool(decoded)
        } else if let decoded = try? value.decode(Double.self) {
            self = .number(decoded)
        } else if let decoded = try? value.decode(String.self) {
            self = .string(decoded)
        } else if let decoded = try? value.decode([String: JSONValue].self) {
            self = .object(decoded)
        } else {
            self = .array(try value.decode([JSONValue].self))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case let .string(item): try value.encode(item)
        case let .number(item): try value.encode(item)
        case let .bool(item): try value.encode(item)
        case let .object(item): try value.encode(item)
        case let .array(item): try value.encode(item)
        case .null: try value.encodeNil()
        }
    }
}

public struct DebugClientRegistration: Codable, Sendable {
    public let clientId: String
    public let appName: String
    public let bundleId: String
    public let platform: String
    public let osVersion: String
    public let capabilities: [String: DebugCapability]

    enum CodingKeys: String, CodingKey {
        case clientId = "client_id"
        case appName = "app_name"
        case bundleId = "bundle_id"
        case platform
        case osVersion = "os_version"
        case capabilities
    }
}

public struct DebugCommand: Codable, Sendable {
    public let id: String
    public let name: String
    public let payload: [String: JSONValue]
    public let expiresAt: Double

    enum CodingKeys: String, CodingKey {
        case id, name, payload
        case expiresAt = "expires_at"
    }
}

public struct DebugEvent: Codable, Sendable {
    public let id: String
    public let module: String
    public let kind: String
    public let payload: [String: JSONValue]

    public init(
        id: String = UUID().uuidString,
        module: String,
        kind: String,
        payload: [String: JSONValue]
    ) {
        self.id = id
        self.module = module
        self.kind = kind
        self.payload = payload
    }
}

struct DebugCommandResponse: Decodable {
    let commands: [DebugCommand]
}
