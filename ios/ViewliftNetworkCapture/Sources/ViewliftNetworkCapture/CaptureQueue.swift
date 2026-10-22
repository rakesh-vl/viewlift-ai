import Foundation

/// Bounded FIFO. Drops oldest under pressure and tracks drop count.
public final class CaptureQueue: CaptureQueuing, @unchecked Sendable {
    private let maxDepth: Int
    private var items: [CapturedHTTPExchange] = []
    private let lock = NSLock()
    private var drops = 0

    public init(maxDepth: Int = 64) {
        self.maxDepth = max(1, maxDepth)
    }

    public var dropCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return drops
    }

    public func enqueue(_ item: CapturedHTTPExchange) {
        lock.lock()
        defer { lock.unlock() }
        if items.count >= maxDepth {
            items.removeFirst()
            drops += 1
        }
        items.append(item)
    }

    public func dequeue() -> CapturedHTTPExchange? {
        lock.lock()
        defer { lock.unlock() }
        guard !items.isEmpty else { return nil }
        return items.removeFirst()
    }

    public func removeAll() {
        lock.lock()
        defer { lock.unlock() }
        items.removeAll()
    }
}
