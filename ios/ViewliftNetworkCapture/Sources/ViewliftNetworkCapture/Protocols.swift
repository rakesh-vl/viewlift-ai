import Foundation

/// Abstracts on-device redaction before bytes leave the device (DIP).
public protocol Redacting: Sendable {
    func redact(headers: [String: String]) -> [String: String]
    func redact(jsonData: Data) -> Data
}

/// Bounded in-memory delivery queue (ISP).
public protocol CaptureQueuing: AnyObject, Sendable {
    var dropCount: Int { get }
    func enqueue(_ item: CapturedHTTPExchange)
    func dequeue() -> CapturedHTTPExchange?
    func removeAll()
}

/// Package-owned upload transport excluded from URLProtocol interception.
public protocol CaptureTransporting: AnyObject, Sendable {
    func start(configuration: CaptureConfiguration) async throws
    func stop()
    func send(flow: CapturedHTTPExchange) async throws
    func send(log: CapturedLogEvent) async throws
}

/// Pinned command/event channel used by the browser-owned remote debug suite.
public protocol DebugTransporting: AnyObject, Sendable {
    func register(client: DebugClientRegistration) async throws
    func pollCommands(clientId: String) async throws -> [DebugCommand]
    func send(event: DebugEvent, clientId: String) async throws
}

/// Public capture lifecycle (SRP).
public protocol CaptureServing: AnyObject {
    func start(configuration: CaptureConfiguration)
    func stop()
    var isRunning: Bool { get }
    var dropCount: Int { get }
}

/// Public structured logging that also uploads to the dashboard.
public protocol LogServing: AnyObject {
    func log(level: CaptureLogLevel, subsystem: String, message: String, requestId: String?)
}
