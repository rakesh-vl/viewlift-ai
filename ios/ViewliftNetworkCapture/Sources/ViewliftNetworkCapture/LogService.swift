import Foundation

/// Structured log upload companion for CaptureService.
public final class LogService: LogServing, @unchecked Sendable {
    public static let shared = LogService()

    /// Debug-only sink so a hang report can include lines the app logged through the SDK.
    static var consoleHook: ((String, String, String) -> Void)?

    private let lock = NSLock()
    private var transportOverride: CaptureTransporting?
    #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
    /// Bounds console uploads in flight. Lines past the cap stay in Xcode only, so a failing
    /// transport cannot feed back into the stdout/stderr tee and grow without limit.
    private static let maxPendingConsoleSends = 64
    private var pendingConsoleSends = 0
    private let consoleSubsystem = Bundle.main.bundleIdentifier ?? "app"
    #endif

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
        LogService.consoleHook?(level.rawValue, subsystem, message)
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

    /// Uploads one stdout or stderr `print` captured by `HangConsoleBuffer`.
    /// A multiline string stays one message. `source` is `stdout` or `stderr`.
    /// The text still reaches Xcode because the tee writes it back.
    func uploadConsoleLine(source: String, message: String) {
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let clean = String(message.prefix(4_000))
        lock.lock()
        let transport = transportOverride
        let canSend = transport != nil && pendingConsoleSends < LogService.maxPendingConsoleSends
        if canSend {
            pendingConsoleSends += 1
        }
        lock.unlock()
        guard canSend, let transport else { return }
        let event = CapturedLogEvent(
            level: source == "stderr" ? .error : .info,
            subsystem: consoleSubsystem,
            message: clean,
            source: source
        )
        Task {
            try? await transport.send(log: event)
            self.finishConsoleSend()
        }
        #else
        _ = source
        _ = message
        #endif
    }

    #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
    private func finishConsoleSend() {
        lock.lock()
        pendingConsoleSends = max(0, pendingConsoleSends - 1)
        lock.unlock()
    }
    #endif
}
