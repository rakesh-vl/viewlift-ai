import Foundation

/// Shared runtime state for URLProtocol + services (package-internal).
enum CaptureRuntime {
    static let recursionKey = "X-Viewlift-Capture-Internal"
    static let recursionValue = "1"

    private static let lock = NSLock()
    private static var _active = false
    private static var _configuration: CaptureConfiguration?
    private static var _redactor: Redacting = HeaderJSONRedactor()
    private static var _queue: CaptureQueuing = CaptureQueue()
    private static var _onCapture: ((CapturedHTTPExchange) -> Void)?

    static var isActive: Bool {
        lock.lock()
        defer { lock.unlock() }
        return _active
    }

    static var configuration: CaptureConfiguration? {
        lock.lock()
        defer { lock.unlock() }
        return _configuration
    }

    static var redactor: Redacting {
        lock.lock()
        defer { lock.unlock() }
        return _redactor
    }

    static var queue: CaptureQueuing {
        lock.lock()
        defer { lock.unlock() }
        return _queue
    }

    static func activate(
        configuration: CaptureConfiguration,
        redactor: Redacting,
        queue: CaptureQueuing,
        onCapture: @escaping (CapturedHTTPExchange) -> Void
    ) {
        lock.lock()
        _configuration = configuration
        _redactor = redactor
        _queue = queue
        _onCapture = onCapture
        _active = true
        lock.unlock()
    }

    static func deactivate() {
        lock.lock()
        _active = false
        _configuration = nil
        _onCapture = nil
        lock.unlock()
    }

    static func publish(_ exchange: CapturedHTTPExchange) {
        lock.lock()
        let handler = _onCapture
        let queue = _queue
        lock.unlock()
        queue.enqueue(exchange)
        handler?(exchange)
    }

    static func isInternal(_ request: URLRequest) -> Bool {
        request.value(forHTTPHeaderField: recursionKey) == recursionValue
    }

    static func markInternal(_ request: inout URLRequest) {
        request.setValue(recursionValue, forHTTPHeaderField: recursionKey)
    }
}
