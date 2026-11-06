#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

/// A tracked object plus where it was created, so a leak can point back at its origin.
struct MemoryTrackedRecord: @unchecked Sendable {
    weak var value: AnyObject?
    let id: String
    let className: String
    let createdAt: Date
    let context: String
    let screen: String
    let symbols: [String]
}

/// Tracks view controllers from `viewDidLoad`, flags the ones still alive after dismissal,
/// and streams live memory activity to the dashboard.
public final class MemoryDebugService: @unchecked Sendable {
    public static let shared = MemoryDebugService()

    public static let leakDelay: TimeInterval = 3
    static let maxLeaks = 10
    static let maxActivity = 200
    static let liveInterval: TimeInterval = 1
    static let creationFrameLimit = 24
    static let leaksReplyBudget = 420_000
    static let liveDefaultsKey = "viewlift.memory.live"

    private struct Activity {
        let kind: String
        let className: String
        let id: String
        let time: TimeInterval
    }

    private let lock = NSLock()
    private var tracked: [String: MemoryTrackedRecord] = [:]
    private var leaks: [[String: JSONValue]] = []
    private var leakIds: Set<String> = []
    private var activity: [Activity] = []
    private var outbox: [DebugEvent] = []
    private var live = false
    private var lastLive = Date.distantPast
    private var previousHeap: [String: Int] = [:]
    private var installed = false

    private init() {}

    public func installLifecycleTracking() {
        #if canImport(UIKit)
        lock.lock()
        guard !installed else {
            lock.unlock()
            return
        }
        installed = true
        lock.unlock()
        UIViewController.viewliftInstallLeakTracking()
        #endif
        if UserDefaults.standard.bool(forKey: Self.liveDefaultsKey) {
            startLiveTracking()
        }
    }

    /// Registers any object, for example a view model, so it shows in the graph and live feed.
    public func track(_ object: AnyObject, context: String = "registered") {
        let id = RetentionInspector.objectId(object)
        lock.lock()
        let known = tracked[id]?.value === object
        lock.unlock()
        guard !known else { return }
        let symbols = Array(Thread.callStackSymbols.dropFirst(2).prefix(Self.creationFrameLimit))
        let screen = Thread.isMainThread ? HangScreen.current().className : ""
        lock.lock()
        defer { lock.unlock() }
        let record = MemoryTrackedRecord(
            value: object,
            id: id,
            className: RetentionInspector.typeName(object),
            createdAt: Date(),
            context: context,
            screen: screen,
            symbols: symbols
        )
        tracked[id] = record
        noteActivityLocked("loaded", record: record)
    }

    /// Reports a leak when `object` is still alive `delay` seconds from now.
    public func expectRelease(
        of object: AnyObject,
        after delay: TimeInterval = MemoryDebugService.leakDelay,
        context: String = "expected-release"
    ) {
        track(object, context: context)
        let id = RetentionInspector.objectId(object)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self, weak object] in
            guard let self, let object else { return }
            self.reportLeak(object, id: id, waited: delay)
        }
    }

    /// Starts the live feed and the rolling screen recording used by leak replays.
    public func startLiveTracking() {
        lock.lock()
        live = true
        lastLive = .distantPast
        lock.unlock()
        UserDefaults.standard.set(true, forKey: Self.liveDefaultsKey)
        LeakReplayRecorder.shared.start()
    }

    public func stopLiveTracking() {
        lock.lock()
        live = false
        activity.removeAll()
        lock.unlock()
        UserDefaults.standard.set(false, forKey: Self.liveDefaultsKey)
        LeakReplayRecorder.shared.stop()
    }

    func checkAfterDelay(_ object: AnyObject, context: String) {
        expectRelease(of: object, after: Self.leakDelay, context: context)
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        pruneLocked()
        let rows = tracked.values.map(Self.describe)
        let summaries = leaks.map(LeakReport.summary)
        let isLive = live
        lock.unlock()
        return [
            "tracked": .array(rows),
            "suspected_leaks": .array(summaries),
            "tracked_count": .number(Double(rows.count)),
            "leak_count": .number(Double(summaries.count)),
            "live": .bool(isLive),
            "footprint_bytes": .number(Double(MemoryFootprint.current()))
        ]
    }

    func leakList() -> [String: JSONValue] {
        lock.lock()
        pruneLocked()
        var copy = Array(leaks.reversed())
        lock.unlock()
        LeakReport.trimClips(&copy, budget: Self.leaksReplyBudget)
        return [
            "count": .number(Double(copy.count)),
            "events": .array(copy.map(JSONValue.object)),
            "note": .string(
                "A leak is an object still alive \(Int(Self.leakDelay)) s after its screen was dismissed. "
                    + "Start live tracking before reproducing to get a replay of the steps."
            )
        ]
    }

    func liveState(enabled: Bool) -> [String: JSONValue] {
        if enabled {
            startLiveTracking()
        } else {
            stopLiveTracking()
        }
        return ["live": .bool(enabled), "recording": .bool(LeakReplayRecorder.shared.isRecording)]
    }

    @MainActor
    func graph() -> [String: JSONValue] {
        lock.lock()
        pruneLocked()
        let alive = tracked.values.compactMap { record in record.value.map { (record, $0) } }
        let leaked = leakIds
        lock.unlock()
        return MemoryGraphBuilder.build(live: alive, leaked: leaked)
    }

    func heap() -> [String: JSONValue] {
        let started = Date()
        let rows = HeapCensus.snapshot()
        let duration = Date().timeIntervalSince(started)
        lock.lock()
        let previous = previousHeap
        previousHeap = Dictionary(rows.map { ($0.className, $0.count) }, uniquingKeysWith: +)
        lock.unlock()
        return HeapCensusFormatter.payload(rows: rows, previous: previous, duration: duration)
    }

    /// Pending leak events plus, in live mode, one live sample per second.
    func drainEvents(now: Date = Date()) -> [DebugEvent] {
        lock.lock()
        defer { lock.unlock() }
        pruneLocked()
        var out = outbox
        outbox.removeAll()
        if live, now.timeIntervalSince(lastLive) >= Self.liveInterval {
            out.append(DebugEvent(module: "memory", kind: "live", payload: livePayloadLocked(now)))
            activity.removeAll()
            lastLive = now
        }
        return out
    }

    private func reportLeak(_ object: AnyObject, id: String, waited: TimeInterval) {
        lock.lock()
        guard !leakIds.contains(id), let record = tracked[id], record.value === object else {
            lock.unlock()
            return
        }
        leakIds.insert(id)
        let others = tracked.values.compactMap { item in item.value.map { (id: item.id, object: $0) } }
        lock.unlock()
        let report = LeakReport.make(object: object, record: record, waited: waited, others: others)
        lock.lock()
        leaks.append(report)
        if leaks.count > Self.maxLeaks {
            leaks.removeFirst(leaks.count - Self.maxLeaks)
        }
        outbox.append(DebugEvent(module: "memory", kind: "leak", payload: report))
        noteActivityLocked("leak", record: record)
        lock.unlock()
    }

    private func pruneLocked() {
        for (id, record) in tracked where record.value == nil {
            tracked.removeValue(forKey: id)
            guard leakIds.remove(id) != nil else {
                noteActivityLocked("released", record: record)
                continue
            }
            noteActivityLocked("released_late", record: record)
            for index in leaks.indices where leaks[index]["object_id"] == .string(id) {
                leaks[index]["status"] = .string("released_late")
                leaks[index]["released_after_seconds"] = .number(Date().timeIntervalSince(record.createdAt))
            }
        }
    }

    private func noteActivityLocked(_ kind: String, record: MemoryTrackedRecord) {
        guard live else { return }
        activity.append(Activity(
            kind: kind,
            className: record.className,
            id: record.id,
            time: Date().timeIntervalSince1970
        ))
        if activity.count > Self.maxActivity {
            activity.removeFirst(activity.count - Self.maxActivity)
        }
    }

    private func livePayloadLocked(_ now: Date) -> [String: JSONValue] {
        var counts: [String: Int] = [:]
        for record in tracked.values {
            counts[record.className, default: 0] += 1
        }
        let classes = counts.sorted { $0.value > $1.value }.prefix(40).map { item -> JSONValue in
            .object(["class": .string(item.key), "count": .number(Double(item.value))])
        }
        let openLeaks = leaks.filter { $0["status"] == .string("alive") }.count
        return [
            "timestamp": .number(now.timeIntervalSince1970),
            "footprint_bytes": .number(Double(MemoryFootprint.current())),
            "tracked_count": .number(Double(tracked.count)),
            "leak_count": .number(Double(openLeaks)),
            "recording": .bool(LeakReplayRecorder.shared.isRecording),
            "classes": .array(classes),
            "activity": .array(activity.map { item in
                .object([
                    "kind": .string(item.kind),
                    "class": .string(item.className),
                    "id": .string(item.id),
                    "timestamp": .number(item.time)
                ])
            })
        ]
    }

    private static func describe(_ item: MemoryTrackedRecord) -> JSONValue {
        .object([
            "id": .string(item.id),
            "class": .string(item.className),
            "context": .string(item.context),
            "screen": .string(item.screen),
            "age_seconds": .number(Date().timeIntervalSince(item.createdAt))
        ])
    }
}

#if canImport(UIKit)
private extension UIViewController {
    static func viewliftInstallLeakTracking() {
        swap(#selector(UIViewController.viewDidLoad), #selector(UIViewController.viewliftViewDidLoad))
        swap(
            #selector(UIViewController.viewDidDisappear(_:)),
            #selector(UIViewController.viewliftViewDidDisappear(_:))
        )
    }

    static func swap(_ original: Selector, _ replacement: Selector) {
        guard let source = class_getInstanceMethod(UIViewController.self, original),
              let target = class_getInstanceMethod(UIViewController.self, replacement) else {
            return
        }
        method_exchangeImplementations(source, target)
    }

    @objc func viewliftViewDidLoad() {
        viewliftViewDidLoad()
        guard !RetentionInspector.isSystemClass(String(describing: type(of: self))) else { return }
        MemoryDebugService.shared.track(self, context: "view-controller")
    }

    @objc func viewliftViewDidDisappear(_ animated: Bool) {
        viewliftViewDidDisappear(animated)
        guard isBeingDismissed || navigationController?.isBeingDismissed == true
            || isMovingFromParent,
            !RetentionInspector.isSystemClass(String(describing: type(of: self))) else {
            return
        }
        MemoryDebugService.shared.checkAfterDelay(
            self,
            context: "dismissed-view-controller"
        )
    }
}
#endif
#endif
