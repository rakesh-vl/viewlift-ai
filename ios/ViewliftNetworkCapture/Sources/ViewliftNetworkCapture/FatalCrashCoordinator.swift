#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ViewliftCaptureObjC

/// Owns fatal crash capture: the signal handler, the uncaught exception hook, recovery of a crash
/// from the last run, and delivery to the dashboard.
final class FatalCrashCoordinator: @unchecked Sendable {
    static let shared = FatalCrashCoordinator()
    static let maxEvents = 80
    /// How long an uncaught exception waits for the dashboard before the app is allowed to die.
    static let liveSendSeconds = 1.0

    private let lock = NSLock()
    private let store: CrashStore
    private var events: [[String: JSONValue]] = []
    private var undelivered: [CrashStore.Entry] = []
    private var installed = false

    init(store: CrashStore = .shared) {
        self.store = store
    }

    static var executableName: String {
        Bundle.main.executableURL?.lastPathComponent ?? ""
    }

    func install() {
        lock.lock()
        if installed {
            lock.unlock()
            return
        }
        installed = true
        lock.unlock()
        // The handler truncates its file on install, so a record left by the last run is read first.
        if let promoted = store.promotePendingRecord(executable: Self.executableName) {
            store.save(attachingReplay(to: promoted))
        } else {
            store.discardLiveReplay()
        }
        let recovered = store.unsent()
        lock.lock()
        undelivered = recovered
        events = recovered.map(\.event)
        lock.unlock()
        _ = VLCCrashSignalsInstall(store.pendingURL.path)
    }

    /// Crashes recorded or recovered since launch, oldest first, for `crash.list`.
    func eventList() -> [[String: JSONValue]] {
        lock.lock()
        defer { lock.unlock() }
        return events
    }

    /// Events still waiting for the dashboard. The bridge loop sends them and reports back.
    func pendingDelivery() -> [CrashStore.Entry] {
        lock.lock()
        defer { lock.unlock() }
        return undelivered
    }

    func markDelivered(id: String) {
        lock.lock()
        undelivered.removeAll { $0.id == id }
        lock.unlock()
        store.remove(id: id)
    }

    /// Runs on the crashing thread. Saves the exception to disk first, then tries to reach the dashboard.
    func recordException(_ exception: NSException) {
        VLCCrashSignalsSuppress()
        CrashContextWriter.shared.writeNow()
        CrashReplayRecorder.shared.flush()
        let entry = attachingReplay(to: Self.entry(for: exception))
        store.save(entry)
        lock.lock()
        events.append(entry.event)
        if events.count > Self.maxEvents {
            events.removeFirst(events.count - Self.maxEvents)
        }
        undelivered.append(entry)
        lock.unlock()
        if DebugBridgeService.shared.deliverCrashNow(entry, wait: Self.liveSendSeconds) {
            markDelivered(id: entry.id)
        }
    }

    /// Live replay folder. Call `install()` first, so a crash's frames are frozen before recording restarts.
    var replayDirectory: URL {
        store.replayDirectory
    }

    /// Crashes whose replay folder is still on disk, waiting to be encoded and uploaded.
    func pendingReplays() -> [(id: String, folder: URL)] {
        store.replayFolders().map { ($0.lastPathComponent, $0) }
    }

    func removeReplay(id: String) {
        store.removeReplay(id: id)
    }

    /// Adds what the app was doing and, when frames exist, a pending replay to the crash event.
    private func attachingReplay(to entry: CrashStore.Entry) -> CrashStore.Entry {
        let frozen = store.freezeReplay(id: entry.id)
        var event = entry.event
        if let context = frozen.context {
            event["context"] = .object(context)
        }
        if let replay = frozen.replay {
            event["replay"] = .object(replay)
            store.writeReplayCrashInfo(id: entry.id, info: [
                "pid": event["pid"] ?? .null,
                "timestamp": event["timestamp"] ?? .null
            ])
        }
        return CrashStore.Entry(id: entry.id, event: event)
    }

    static func entry(for exception: NSException) -> CrashStore.Entry {
        let addresses = exception.callStackReturnAddresses.map { $0.uint64Value }
        let record = ExceptionCrashRecord.make(addresses: addresses)
        let id = UUID().uuidString
        var event = CrashEventBuilder.event(
            from: record,
            executable: executableName,
            id: id,
            previousSession: false
        )
        event["kind"] = .string("exception")
        event["source"] = .string("sdk-exception")
        event["name"] = .string(exception.name.rawValue)
        event["reason"] = .string(exception.reason ?? "")
        event["frames"] = .array(mergedFrames(event["frames"], symbols: exception.callStackSymbols))
        return CrashStore.Entry(id: id, event: event)
    }

    /// Keeps the address, image, and UUID from the record and adds the symbol names the runtime resolved.
    private static func mergedFrames(_ frames: JSONValue?, symbols: [String]) -> [JSONValue] {
        guard case let .array(rows)? = frames else { return [] }
        let named = CrashHangRecorder.parseFrames(symbols)
        return rows.enumerated().map { index, row in
            guard case var .object(frame) = row, index < named.count else { return row }
            for key in ["symbol", "class", "file", "line"] {
                if let value = named[index][key] { frame[key] = value }
            }
            return .object(frame)
        }
    }
}
#endif
