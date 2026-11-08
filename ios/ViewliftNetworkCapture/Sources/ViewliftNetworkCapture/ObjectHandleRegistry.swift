#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

protocol ObjectHandleRegistering: AnyObject {
    func register(_ object: AnyObject) -> String
    func object(for handle: String) -> AnyObject?
    func removeAll()
}

/// Opaque handles for objects the dashboard explores. The browser never sees a raw address,
/// so it can only reach objects the SDK handed out. Entries are weak unless the class refuses
/// weak references, and the oldest entries are evicted past `capacity`.
final class ObjectHandleRegistry: ObjectHandleRegistering, @unchecked Sendable {
    static let shared = ObjectHandleRegistry()

    private final class Entry {
        weak var weakValue: AnyObject?
        let strongValue: AnyObject?
        var stamp: UInt64

        init(_ object: AnyObject, stamp: UInt64) {
            if ObjectHandleRegistry.allowsWeakReference(object) {
                weakValue = object
                strongValue = nil
            } else {
                strongValue = object
            }
            self.stamp = stamp
        }

        var value: AnyObject? { strongValue ?? weakValue }
    }

    let capacity: Int
    private let lock = NSLock()
    private var entries: [String: Entry] = [:]
    private var byIdentity: [ObjectIdentifier: String] = [:]
    private var clock: UInt64 = 0

    init(capacity: Int = 2_000) {
        self.capacity = max(8, capacity)
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return entries.count
    }

    func register(_ object: AnyObject) -> String {
        let identity = ObjectIdentifier(object)
        lock.lock()
        defer { lock.unlock() }
        clock += 1
        if let handle = byIdentity[identity], let entry = entries[handle], entry.value === object {
            entry.stamp = clock
            return handle
        }
        let handle = "h\(clock)"
        entries[handle] = Entry(object, stamp: clock)
        byIdentity[identity] = handle
        if entries.count > capacity {
            evictLocked()
        }
        return handle
    }

    func object(for handle: String) -> AnyObject? {
        lock.lock()
        defer { lock.unlock() }
        guard let entry = entries[handle] else { return nil }
        guard let value = entry.value else {
            entries[handle] = nil
            return nil
        }
        clock += 1
        entry.stamp = clock
        return value
    }

    func removeAll() {
        lock.lock()
        entries.removeAll()
        byIdentity.removeAll()
        lock.unlock()
    }

    /// Drops released objects first, then the least recently used tenth.
    private func evictLocked() {
        for (handle, entry) in entries where entry.value == nil {
            entries[handle] = nil
        }
        let excess = entries.count - capacity
        guard excess > 0 else {
            rebuildIdentityLocked()
            return
        }
        let batch = max(excess, capacity / 10)
        let oldest = entries.sorted { $0.value.stamp < $1.value.stamp }.prefix(batch)
        for (handle, _) in oldest {
            entries[handle] = nil
        }
        rebuildIdentityLocked()
    }

    private func rebuildIdentityLocked() {
        var map: [ObjectIdentifier: String] = [:]
        for (handle, entry) in entries {
            if let value = entry.value {
                map[ObjectIdentifier(value)] = handle
            }
        }
        byIdentity = map
    }

    /// Forming a weak reference to a class that overrides `allowsWeakReference` to NO aborts the process.
    static func allowsWeakReference(_ object: AnyObject) -> Bool {
        let selector = NSSelectorFromString("allowsWeakReference")
        guard let cls = object_getClass(object),
              let method = class_getInstanceMethod(cls, selector) else {
            return true
        }
        typealias Getter = @convention(c) (AnyObject, Selector) -> Bool
        let call = unsafeBitCast(method_getImplementation(method), to: Getter.self)
        return call(object, selector)
    }
}
#endif
