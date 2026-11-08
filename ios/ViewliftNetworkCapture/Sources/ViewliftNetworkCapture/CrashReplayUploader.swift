#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Splits a replay video into pieces that fit under the dashboard's 512 KB event limit once base64 encoded.
enum CrashReplayChunker {
    /// 225 000 raw bytes become 300 000 base64 characters.
    static let chunkBytes = 225_000

    static func chunkCount(byteCount: Int, chunkBytes: Int = chunkBytes) -> Int {
        guard byteCount > 0, chunkBytes > 0 else { return 0 }
        return (byteCount + chunkBytes - 1) / chunkBytes
    }

    static func chunk(_ data: Data, index: Int, chunkBytes: Int = chunkBytes) -> Data {
        let start = index * chunkBytes
        guard index >= 0, start < data.count else { return Data() }
        return data.subdata(in: start..<min(data.count, start + chunkBytes))
    }
}

/// Encodes each frozen replay on the launch after the crash and sends it to the dashboard in chunks.
/// Progress is kept on disk, so an upload cut short by the app closing resumes where it stopped.
final class CrashReplayUploader: @unchecked Sendable {
    static let shared = CrashReplayUploader()
    static let crashFileName = "crash.json"
    static let progressFileName = "progress.json"
    static let posterBytes = 60_000

    private let coordinator: FatalCrashCoordinator

    init(coordinator: FatalCrashCoordinator = .shared) {
        self.coordinator = coordinator
    }

    /// Replays are sent only after their crash event, so the dashboard has a card to attach them to.
    func sendPending(transport: DebugTransporting, clientId: String) async throws {
        let waiting = Set(coordinator.pendingDelivery().map(\.id))
        for (id, folder) in coordinator.pendingReplays() where !waiting.contains(id) {
            let crash = Self.readJSON(folder.appendingPathComponent(Self.crashFileName)) ?? [:]
            let video: CrashReplayVideo
            do {
                video = try await Self.encodeIfNeeded(folder: folder, crash: crash)
            } catch {
                try await transport.send(event: failure(id: id, crash: crash, error: error), clientId: clientId)
                coordinator.removeReplay(id: id)
                continue
            }
            let job = Job(id: id, folder: folder, crash: crash, video: video)
            try await upload(job, transport: transport, clientId: clientId)
            coordinator.removeReplay(id: id)
        }
    }

    private static func encodeIfNeeded(folder: URL, crash: [String: JSONValue]) async throws -> CrashReplayVideo {
        let crashTime: TimeInterval? = {
            if case let .number(value)? = crash["timestamp"], value > 0 { return value }
            return nil
        }()
        return try await Task.detached(priority: .utility) {
            try CrashReplayEncoder.encode(folder: folder, crashTime: crashTime)
        }.value
    }

    private struct Job {
        let id: String
        let folder: URL
        let crash: [String: JSONValue]
        let video: CrashReplayVideo
    }

    private func upload(_ job: Job, transport: DebugTransporting, clientId: String) async throws {
        let (id, folder, crash, video) = (job.id, job.folder, job.crash, job.video)
        let data = try Data(contentsOf: video.url)
        let total = CrashReplayChunker.chunkCount(byteCount: data.count)
        let progressURL = folder.appendingPathComponent(Self.progressFileName)
        var next = 0
        if case let .number(value)? = Self.readJSON(progressURL)?["next"] {
            next = min(total, max(0, Int(safe: value)))
        }
        for index in next..<total {
            var payload = base(id: id, crash: crash)
            payload["index"] = .number(Double(index))
            payload["total"] = .number(Double(total))
            payload["bytes"] = .number(Double(data.count))
            payload["mime"] = .string("video/mp4")
            payload["frames"] = .number(Double(video.frames))
            payload["duration_ms"] = .number(video.durationSeconds * 1_000)
            payload["width"] = .number(Double(video.width))
            payload["height"] = .number(Double(video.height))
            payload["base64"] = .string(CrashReplayChunker.chunk(data, index: index).base64EncodedString())
            if index == 0 {
                payload["timeline"] = .array(Self.timeline(folder: folder, crash: crash))
                if let poster = Self.poster(folder: folder) { payload["poster"] = .object(poster) }
            }
            try await transport.send(
                event: DebugEvent(id: "\(id)-replay-\(index)", module: "crash", kind: "replay-chunk", payload: payload),
                clientId: clientId
            )
            if let encoded = try? JSONEncoder().encode(["next": JSONValue.number(Double(index + 1))]) {
                try? encoded.write(to: progressURL, options: .atomic)
            }
        }
    }

    private func base(id: String, crash: [String: JSONValue]) -> [String: JSONValue] {
        ["crash_id": .string(id), "pid": crash["pid"] ?? .null, "timestamp": crash["timestamp"] ?? .null]
    }

    private func failure(id: String, crash: [String: JSONValue], error: Error) -> DebugEvent {
        var payload = base(id: id, crash: crash)
        payload["error"] = .string(String(describing: error))
        return DebugEvent(id: "\(id)-replay-failed", module: "crash", kind: "replay-failed", payload: payload)
    }

    /// Frame times relative to the crash, with the screen and any touches, so the dashboard can seek.
    static func timeline(folder: URL, crash: [String: JSONValue]) -> [JSONValue] {
        let slots = CrashReplayRing.load(from: folder)
        guard let start = slots.first?.time else { return [] }
        var crashTime = slots.last?.time ?? start
        if case let .number(value)? = crash["timestamp"], value > 0 { crashTime = value }
        return slots.map { slot in
            .object([
                "offset_ms": .number((slot.time - start) * 1_000),
                "before_crash_ms": .number(max(0, crashTime - slot.time) * 1_000),
                "screen_class": .string(slot.screen),
                "title": .string(slot.title),
                "touches": .array(slot.touches.map { touch in
                    .object([
                        "phase": .string(touch.phase),
                        "x": .number(touch.pointX),
                        "y": .number(touch.pointY),
                        "label": .string(touch.label)
                    ])
                })
            ])
        }
    }

    /// The last frame before the crash, so the card and an agent see the final screen without the video.
    static func poster(folder: URL) -> [String: JSONValue]? {
        guard let last = CrashReplayRing.load(from: folder).last,
              let data = try? Data(contentsOf: CrashReplayRing.frameURL(folder, slot: last.slot)),
              data.count <= posterBytes else {
            return nil
        }
        return ["mime": .string("image/jpeg"), "base64": .string(data.base64EncodedString())]
    }

    static func readJSON(_ url: URL) -> [String: JSONValue]? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode([String: JSONValue].self, from: data)
    }
}
#endif
