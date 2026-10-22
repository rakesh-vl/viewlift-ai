import Foundation

public enum CaptureLogLevel: String, Sendable, Codable {
    case debug
    case info
    case warning
    case error
    case fault
}

public struct CapturedHTTPExchange: Sendable, Codable {
    public var id: String
    public var url: String
    public var method: String
    public var host: String
    public var path: String
    public var status: Int?
    public var durationMs: Double?
    public var protocolName: String
    public var requestHeaders: [String: String]
    public var requestBodyBase64: String?
    public var responseHeaders: [String: String]
    public var responseBodyBase64: String?
    public var error: String?
    public var requestId: String?
    public var contentType: String?
    public var source: String
    public var extraRedactionKeys: [String]

    public init(
        id: String = UUID().uuidString,
        url: String,
        method: String,
        host: String,
        path: String,
        status: Int?,
        durationMs: Double?,
        protocolName: String,
        requestHeaders: [String: String],
        requestBodyBase64: String?,
        responseHeaders: [String: String],
        responseBodyBase64: String?,
        error: String?,
        requestId: String?,
        contentType: String?,
        source: String = "SDK",
        extraRedactionKeys: [String] = []
    ) {
        self.id = id
        self.url = url
        self.method = method
        self.host = host
        self.path = path
        self.status = status
        self.durationMs = durationMs
        self.protocolName = protocolName
        self.requestHeaders = requestHeaders
        self.requestBodyBase64 = requestBodyBase64
        self.responseHeaders = responseHeaders
        self.responseBodyBase64 = responseBodyBase64
        self.error = error
        self.requestId = requestId
        self.contentType = contentType
        self.source = source
        self.extraRedactionKeys = extraRedactionKeys
    }

    enum CodingKeys: String, CodingKey {
        case id, url, method, host, path, status
        case durationMs = "duration_ms"
        case protocolName = "protocol"
        case requestHeaders = "request_headers"
        case requestBodyBase64 = "request_body"
        case responseHeaders = "response_headers"
        case responseBodyBase64 = "response_body"
        case error
        case requestId = "request_id"
        case contentType = "content_type"
        case source
        case extraRedactionKeys = "extra_redaction_keys"
    }
}

public struct CapturedLogEvent: Sendable, Codable {
    public var id: String
    public var level: CaptureLogLevel
    public var subsystem: String
    public var message: String
    public var requestId: String?
    public var source: String

    public init(
        id: String = UUID().uuidString,
        level: CaptureLogLevel,
        subsystem: String,
        message: String,
        requestId: String? = nil,
        source: String = "sdk"
    ) {
        self.id = id
        self.level = level
        self.subsystem = subsystem
        self.message = message
        self.requestId = requestId
        self.source = source
    }

    enum CodingKeys: String, CodingKey {
        case id, level, subsystem, message, source
        case requestId = "request_id"
    }
}
