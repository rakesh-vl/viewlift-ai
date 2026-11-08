#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Crash events that have been recorded but not yet delivered to the dashboard.
/// They live on disk so a crash that kills the process still arrives on the next launch.
final class CrashStore: @unchecked Sendable {
    static let shared = CrashStore()
    static let maxUnsent = 10
    static let maxReplays = 3

    struct Entry {
        let id: String
        let event: [String: JSONValue]
    }

    let directory: URL
    private let lock = NSLock()

    init(directory: URL = CrashStore.defaultDirectory()) {
        self.directory = directory
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("ViewliftCrash", isDirectory: true)
    }

    /// File the C signal handler writes to.
    var pendingURL: URL {
        directory.appendingPathComponent("pending.crash")
    }

    private var unsentDirectory: URL {
        directory.appendingPathComponent("unsent", isDirectory: true)
    }

    /// Live ring the replay recorder and context writer fill while the app runs.
    var replayDirectory: URL {
        directory.appendingPathComponent("replay", isDirectory: true)
    }

    /// One folder per crash, holding its frozen frames until the video has been uploaded.
    var replaysDirectory: URL {
        directory.appendingPathComponent("replays", isDirectory: true)
    }

    /// Moves a record left by the last run into the unsent list. Call before the handler reopens the file.
    @discardableResult
    func promotePendingRecord(executable: String) -> Entry? {
        lock.lock()
        defer { lock.unlock() }
        defer { try? FileManager.default.removeItem(at: pendingURL) }
        guard let data = try? Data(contentsOf: pendingURL), !data.isEmpty,
              let text = String(data: data, encoding: .utf8),
              let record = CrashRecordParser.parse(text) else {
            return nil
        }
        let id = UUID().uuidString
        let event = CrashEventBuilder.event(from: record, executable: executable, id: id)
        writeLocked(Entry(id: id, event: event))
        return Entry(id: id, event: event)
    }

    func save(_ entry: Entry) {
        lock.lock()
        defer { lock.unlock() }
        writeLocked(entry)
    }

    func unsent() -> [Entry] {
        lock.lock()
        defer { lock.unlock() }
        return files().compactMap { url in
            guard let data = try? Data(contentsOf: url),
                  let event = try? JSONDecoder().decode([String: JSONValue].self, from: data) else {
                try? FileManager.default.removeItem(at: url)
                return nil
            }
            return Entry(id: url.deletingPathExtension().lastPathComponent, event: event)
        }
    }

    func remove(id: String) {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: unsentDirectory.appendingPathComponent("\(id).json"))
    }

    /// Moves the live ring under `replays/<id>` so the next run cannot overwrite it, and returns
    /// the context and a short description of the frames. Only the newest `maxReplays` are kept.
    func freezeReplay(id: String) -> (context: [String: JSONValue]?, replay: [String: JSONValue]?) {
        lock.lock()
        defer { lock.unlock() }
        let manager = FileManager.default
        guard manager.fileExists(atPath: replayDirectory.path) else { return (nil, nil) }
        try? manager.createDirectory(at: replaysDirectory, withIntermediateDirectories: true)
        let target = replaysDirectory.appendingPathComponent(id, isDirectory: true)
        try? manager.removeItem(at: target)
        guard (try? manager.moveItem(at: replayDirectory, to: target)) != nil else { return (nil, nil) }
        for stale in replayFolders().dropLast(Self.maxReplays) {
            try? manager.removeItem(at: stale)
        }
        let context = (try? Data(contentsOf: target.appendingPathComponent(CrashReplayRing.contextName)))
            .flatMap { try? JSONDecoder().decode([String: JSONValue].self, from: $0) }
        let slots = CrashReplayRing.load(from: target)
        guard let first = slots.first, let last = slots.last else { return (context, nil) }
        let replay: [String: JSONValue] = [
            "status": .string("pending"),
            "frames": .number(Double(slots.count)),
            "duration_ms": .number((last.time - first.time) * 1_000),
            "first_frame_at": .number(first.time),
            "last_frame_at": .number(last.time)
        ]
        return (context, replay)
    }

    /// Pid and crash time, which the uploader needs after the crash event itself has been sent and deleted.
    func writeReplayCrashInfo(id: String, info: [String: JSONValue]) {
        let url = replaysDirectory
            .appendingPathComponent(id, isDirectory: true)
            .appendingPathComponent(CrashReplayUploader.crashFileName)
        guard let data = try? JSONEncoder().encode(info) else { return }
        try? data.write(to: url, options: .atomic)
    }

    /// Frozen replay folders, oldest first.
    func replayFolders() -> [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: replaysDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey]
        )) ?? []
        return urls.filter(\.hasDirectoryPath).sorted { modified($0) < modified($1) }
    }

    /// The last run ended without a crash, so its frames and context describe nothing.
    func discardLiveReplay() {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: replayDirectory)
    }

    func removeReplay(id: String) {
        lock.lock()
        defer { lock.unlock() }
        try? FileManager.default.removeItem(at: replaysDirectory.appendingPathComponent(id, isDirectory: true))
    }

    private func writeLocked(_ entry: Entry) {
        try? FileManager.default.createDirectory(at: unsentDirectory, withIntermediateDirectories: true)
        let url = unsentDirectory.appendingPathComponent("\(entry.id).json")
        guard let data = try? JSONEncoder().encode(entry.event) else { return }
        try? data.write(to: url, options: .atomic)
        let stale = files().dropLast(Self.maxUnsent)
        for item in stale {
            try? FileManager.default.removeItem(at: item)
        }
    }

    /// Oldest first, so the newest crash is the last to be trimmed.
    private func files() -> [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: unsentDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey]
        )) ?? []
        return urls
            .filter { $0.pathExtension == "json" }
            .sorted { modified($0) < modified($1) }
    }

    private func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate)
            ?? .distantPast
    }
}
#endif
