#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Thread-safe fixed-capacity buffer; the oldest element is dropped first.
public final class OTTDebugRingBuffer<Element>: @unchecked Sendable {
    public let capacity: Int
    private let lock = NSLock()
    private var storage: [Element] = []

    public init(capacity: Int) {
        self.capacity = max(1, capacity)
    }

    public func append(_ element: Element) {
        lock.lock()
        storage.append(element)
        if storage.count > capacity {
            storage.removeFirst(storage.count - capacity)
        }
        lock.unlock()
    }

    /// Oldest first.
    public var elements: [Element] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    public var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return storage.count
    }

    public func removeAll() {
        lock.lock()
        storage.removeAll()
        lock.unlock()
    }
}

/// Records posted notifications with redacted `userInfo` for host debug sections.
public final class OTTDebugNotificationRecorder: @unchecked Sendable {
    public struct Entry: Sendable {
        public let name: String
        public let date: Date
        public let info: JSONValue

        public var json: JSONValue {
            .object(["name": .string(name), "at": .number(date.timeIntervalSince1970), "info": info])
        }
    }

    private let buffer: OTTDebugRingBuffer<Entry>
    private let lock = NSLock()
    private var observers: [NSObjectProtocol] = []
    private var counts: [String: Int] = [:]
    private let onRecord: (@Sendable (String) -> Void)?

    /// `onRecord` receives the notification name, for example to call `OTTDebugRegistry.notifyChanged`.
    public init(capacity: Int = 100, onRecord: (@Sendable (String) -> Void)? = nil) {
        buffer = OTTDebugRingBuffer(capacity: capacity)
        self.onRecord = onRecord
    }

    deinit {
        stop()
    }

    public func start(_ names: [Notification.Name]) {
        let tokens = names.map { name in
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { [weak self] note in
                self?.record(note)
            }
        }
        lock.lock()
        observers.append(contentsOf: tokens)
        lock.unlock()
    }

    public func stop() {
        lock.lock()
        let tokens = observers
        observers = []
        lock.unlock()
        tokens.forEach(NotificationCenter.default.removeObserver)
    }

    public func record(_ note: Notification) {
        record(name: note.name.rawValue, info: note.userInfo.map { OTTDebugValue.safe(Self.stringKeyed($0)) } ?? .null)
    }

    public func record(name: String, info: JSONValue = .null, date: Date = Date()) {
        buffer.append(Entry(name: name, date: date, info: info))
        lock.lock()
        counts[name, default: 0] += 1
        lock.unlock()
        onRecord?(name)
    }

    /// Newest first.
    public func entries(limit: Int = 100) -> [JSONValue] {
        buffer.elements.suffix(limit).reversed().map(\.json)
    }

    public func lastDate(of name: String) -> Date? {
        buffer.elements.last { $0.name == name }?.date
    }

    public func counts(names: [String]? = nil) -> [String: JSONValue] {
        lock.lock()
        defer { lock.unlock() }
        let selected = names.map { Set($0) }
        return counts
            .filter { selected?.contains($0.key) ?? true }
            .mapValues { .number(Double($0)) }
    }

    public func clear() {
        buffer.removeAll()
        lock.lock()
        counts = [:]
        lock.unlock()
    }

    private static func stringKeyed(_ info: [AnyHashable: Any]) -> [String: Any] {
        var result: [String: Any] = [:]
        for (key, value) in info {
            result[String(describing: key.base)] = value
        }
        return result
    }
}
#endif
