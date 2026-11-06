#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation

/// Builds one leak card: who leaked, where it was created, what retains it, and how to reproduce it.
enum LeakReport {
    static let clipByteBudget = 260_000
    static let clipSeconds: TimeInterval = 14
    static let screenshotByteLimit = 32_000

    static func make(
        object: AnyObject,
        record: MemoryTrackedRecord,
        waited: TimeInterval,
        others: [(id: String, object: AnyObject)]
    ) -> [String: JSONValue] {
        let now = Date()
        let executable = Bundle.main.executableURL?.lastPathComponent ?? ""
        let frames = CrashHangRecorder.parseFrames(record.symbols)
        let screen = HangScreen.current()
        var event: [String: JSONValue] = [
            "id": .string(UUID().uuidString),
            "kind": .string("leak"),
            "status": .string("alive"),
            "timestamp": .number(now.timeIntervalSince1970),
            "object_id": .string(record.id),
            "class": .string(record.className),
            "context": .string(record.context),
            "created_on_screen": .string(record.screen),
            "age_seconds": .number(now.timeIntervalSince(record.createdAt)),
            "waited_seconds": .number(waited),
            "screen_class": .string(screen.className),
            "screen_title": .string(screen.title),
            "executable": .string(executable),
            "footprint_bytes": .number(Double(MemoryFootprint.current())),
            "creation_frames": .array(frames.map(JSONValue.object)),
            "actions": .array(HangInteractionTrail.shared.recent(before: now)),
            "console": .array(HangConsoleBuffer.shared.recent(before: now, seconds: waited + 10)),
            "retention": .object(RetentionInspector.inspect(root: object, others: others))
        ]
        if let focus = HangFrameFocus.suspect(frames: frames, executable: executable) {
            event["suspect"] = .object(focus)
        }
        attachReplay(&event, now: now)
        return event
    }

    /// Small row for `memory.snapshot` without the replay or stacks.
    static func summary(_ event: [String: JSONValue]) -> JSONValue {
        let keys = ["id", "object_id", "class", "context", "status", "age_seconds", "created_on_screen", "timestamp"]
        return .object(event.filter { keys.contains($0.key) })
    }

    /// Keeps replay clips on the newest leaks first until `budget` bytes are used.
    static func trimClips(_ events: inout [[String: JSONValue]], budget: Int) {
        var used = 0
        for index in events.indices {
            let cost = clipBytes(events[index])
            if used + cost <= budget {
                used += cost
                continue
            }
            events[index].removeValue(forKey: "replay")
            events[index]["replay_note"] = .string("Replay omitted so newer leaks keep theirs. Use the leak event.")
        }
    }

    private static func attachReplay(_ event: inout [String: JSONValue], now: Date) {
        if let clip = LeakReplayRecorder.shared.clip(before: now, seconds: clipSeconds, byteBudget: clipByteBudget) {
            event["replay"] = .object(clip)
            return
        }
        event["replay_note"] = .string("No replay. Start live tracking before reproducing to record the steps.")
        if let encoded = HangScreenshot.jpegBase64(maxBytes: screenshotByteLimit) {
            event["screenshot"] = .object(["mime": .string("image/jpeg"), "base64": .string(encoded)])
        }
    }

    private static func clipBytes(_ event: [String: JSONValue]) -> Int {
        guard case let .object(replay) = event["replay"], case let .array(frames) = replay["frames"] else {
            return 0
        }
        return frames.reduce(0) { total, frame in
            guard case let .object(row) = frame, case let .string(text) = row["base64"] else { return total }
            return total + text.utf8.count
        }
    }
}

/// Physical footprint, the number Xcode's memory gauge and jetsam use.
enum MemoryFootprint {
    static func current() -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? info.phys_footprint : 0
    }
}
#endif
