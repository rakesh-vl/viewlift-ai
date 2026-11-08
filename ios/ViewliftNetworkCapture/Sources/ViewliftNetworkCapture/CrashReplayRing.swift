#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// A touch or remote press drawn on a replay frame. Points are normalized to 0...1 in window coordinates.
struct CrashReplayTouch: Codable, Equatable, Sendable {
    var phase: String
    var pointX: Double
    var pointY: Double
    var label: String

    enum CodingKeys: String, CodingKey {
        case phase
        case pointX = "x"
        case pointY = "y"
        case label
    }
}

/// One frame of the pre-crash ring as written to `index.json`.
struct CrashReplaySlot: Codable, Equatable, Sendable {
    var slot: Int
    var time: TimeInterval
    var width: Int
    var height: Int
    var screen: String
    var title: String
    var touches: [CrashReplayTouch]
}

/// The last few seconds of frames, kept on disk so they are still there after the process dies.
/// Each frame overwrites the oldest slot, then `index.json` is rewritten in time order.
final class CrashReplayRing: @unchecked Sendable {
    static let capacity = 40
    static let indexName = "index.json"
    static let contextName = "context.json"

    let directory: URL
    let capacity: Int
    private let lock = NSLock()
    private var slots: [CrashReplaySlot] = []
    private var nextSlot = 0

    init(directory: URL, capacity: Int = CrashReplayRing.capacity) {
        self.directory = directory
        self.capacity = max(1, capacity)
        reset()
    }

    /// Drops whatever an earlier run left behind.
    func reset() {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        slots.removeAll()
        nextSlot = 0
    }

    func append(jpeg: Data, frame: CrashReplaySlot) {
        lock.lock()
        defer { lock.unlock() }
        var entry = frame
        entry.slot = nextSlot
        nextSlot = (nextSlot + 1) % capacity
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        guard (try? jpeg.write(to: Self.frameURL(directory, slot: entry.slot), options: .atomic)) != nil else {
            return
        }
        slots.removeAll { $0.slot == entry.slot }
        slots.append(entry)
        if let data = try? JSONEncoder().encode(slots) {
            try? data.write(to: directory.appendingPathComponent(Self.indexName), options: .atomic)
        }
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return slots.count
    }

    static func frameURL(_ directory: URL, slot: Int) -> URL {
        directory.appendingPathComponent(String(format: "frame-%02d.jpg", slot))
    }

    /// Frames that still have their image, oldest first.
    static func load(from directory: URL) -> [CrashReplaySlot] {
        guard let data = try? Data(contentsOf: directory.appendingPathComponent(indexName)),
              let slots = try? JSONDecoder().decode([CrashReplaySlot].self, from: data) else {
            return []
        }
        return slots
            .filter { FileManager.default.fileExists(atPath: frameURL(directory, slot: $0.slot).path) }
            .sorted { $0.time < $1.time }
    }
}
#endif
