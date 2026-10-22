import Foundation

/// Installs CaptureURLProtocol into URLSessionConfiguration.default / .ephemeral.
///
/// Enable only under `#if DEBUG` or an explicit `VIEWLIFT_CAPTURE_ENABLED` compile flag.
/// App Store / Release builds default to no capture path.
public final class CaptureService: CaptureServing, @unchecked Sendable {
    public static let shared = CaptureService()

    private let lock = NSLock()
    private var running = false
    private var queue = CaptureQueue()
    private let transport: CaptureTransporting
    private var startTask: Task<Void, Never>?

    public init(transport: CaptureTransporting = PinnedHTTPSTransport()) {
        self.transport = transport
    }

    public var isRunning: Bool {
        lock.lock()
        defer { lock.unlock() }
        return running
    }

    public var dropCount: Int {
        queue.dropCount
    }

    public func start(configuration: CaptureConfiguration) {
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        lock.lock()
        if running {
            lock.unlock()
            return
        }
        running = true
        queue = CaptureQueue(maxDepth: configuration.maxQueueDepth)
        let redactor = HeaderJSONRedactor(extraKeys: configuration.extraRedactionKeys)
        CaptureRuntime.activate(
            configuration: configuration,
            redactor: redactor,
            queue: queue
        ) { _ in
            // Drain loop in transport uploads asynchronously; never block app networking.
        }
        LogService.shared.bind(transport: transport)
        ConfigurationInterceptor.install()
        let transport = self.transport
        if let debugTransport = transport as? DebugTransporting {
            DebugBridgeService.shared.start(
                configuration: configuration,
                transport: debugTransport
            )
        }
        startTask = Task {
            do {
                try await transport.start(configuration: configuration)
            } catch {
                // Local capture continues; uploads resume after a successful pair.
            }
        }
        lock.unlock()
        #else
        _ = configuration
        #endif
    }

    public func stop() {
        lock.lock()
        defer { lock.unlock() }
        guard running else { return }
        running = false
        startTask?.cancel()
        startTask = nil
        transport.stop()
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        DebugBridgeService.shared.stop()
        #endif
        CaptureRuntime.deactivate()
        queue.removeAll()
        ConfigurationInterceptor.unregisterSharedSession()
    }
}
