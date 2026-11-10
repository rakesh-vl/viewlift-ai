import Foundation
import ViewliftCaptureObjC

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
    case runtime
    case macro
    case bug
    case push
    case ott
    case crash
    case hang
    case activity
    case playback
    case navigation = "nav"
    case explorer
    case preview
    /// Keeps the last ten seconds of the screen on disk so a crash comes with a replay video.
    case replay
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
        case let .number(item):
            // JSON has no NaN or infinity. One bad frame would fail the whole reply.
            if item.isFinite {
                try value.encode(item)
            } else {
                try value.encodeNil()
            }
        case let .bool(item): try value.encode(item)
        case let .object(item): try value.encode(item)
        case let .array(item): try value.encode(item)
        case .null: try value.encodeNil()
        }
    }
}

public struct DebugFramework: Codable, Sendable, Equatable {
    public let id: String
    public let version: String
    public let name: String
}

public struct DebugClientRegistration: Codable, Sendable {
    public let clientId: String
    public let appName: String
    public let bundleId: String
    public let platform: String
    public let osVersion: String
    public let capabilities: [String: DebugCapability]
    public let appVersion: String
    public let build: String
    public let executable: String
    public let urlSchemes: [String]
    public let frameworks: [DebugFramework]
    public let deviceKey: String
    public let deviceName: String
    public let deviceModel: String
    public let isSimulator: Bool
    public let deviceHint: String
    public let sdkVersion: String
    /// A debugger holds a fatal trap, so a silent app may be paused in Xcode rather than gone.
    public let debuggerAttached: Bool
    /// Who is signed in, when the host has reported a session. Nil until then.
    public let session: CaptureSessionIdentity?

    public init(
        clientId: String,
        appName: String,
        bundleId: String,
        platform: String,
        osVersion: String,
        capabilities: [String: DebugCapability],
        appVersion: String = "",
        build: String = "",
        executable: String = "",
        urlSchemes: [String] = [],
        frameworks: [DebugFramework] = [],
        device: DeviceIdentity? = nil,
        session: CaptureSessionIdentity? = nil
    ) {
        self.deviceKey = device?.deviceKey ?? ""
        self.deviceName = device?.deviceName ?? ""
        self.deviceModel = device?.deviceModel ?? ""
        self.isSimulator = device?.isSimulator ?? false
        self.deviceHint = device?.deviceHint ?? ""
        self.sdkVersion = DeviceIdentity.sdkVersion
        self.debuggerAttached = VLCCrashSignalsDebuggerAttached()
        self.clientId = clientId
        self.appName = appName
        self.bundleId = bundleId
        self.platform = platform
        self.osVersion = osVersion
        self.capabilities = capabilities
        self.appVersion = appVersion
        self.build = build
        self.executable = executable
        self.urlSchemes = urlSchemes
        self.frameworks = frameworks
        self.session = session
    }

    enum CodingKeys: String, CodingKey {
        case clientId = "client_id"
        case appName = "app_name"
        case bundleId = "bundle_id"
        case platform
        case osVersion = "os_version"
        case capabilities
        case appVersion = "app_version"
        case build
        case executable
        case urlSchemes = "url_schemes"
        case frameworks
        case deviceKey = "device_key"
        case deviceName = "device_name"
        case deviceModel = "device_model"
        case isSimulator = "is_simulator"
        case deviceHint = "device_hint"
        case sdkVersion = "sdk_version"
        case debuggerAttached = "debugger_attached"
        case session
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
