import Foundation

/// Structured log upload companion for CaptureService.
public final class LogService: LogServing, @unchecked Sendable {
    public static let shared = LogService()

    private let lock = NSLock()
    private var transportOverride: CaptureTransporting?

    public init(transport: CaptureTransporting? = nil) {
        self.transportOverride = transport
    }

    public func bind(transport: CaptureTransporting) {
        lock.lock()
        transportOverride = transport
        lock.unlock()
    }

    public func log(
        level: CaptureLogLevel,
        subsystem: String,
        message: String,
        requestId: String? = nil
    ) {
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        let event = CapturedLogEvent(
            level: level,
            subsystem: subsystem,
            message: message,
            requestId: requestId,
            source: "sdk"
        )
        lock.lock()
        let transport = transportOverride
        lock.unlock()
        guard let transport else { return }
        Task {
            try? await transport.send(log: event)
        }
        #else
        _ = level
        _ = subsystem
        _ = message
        _ = requestId
        #endif
    }
}
