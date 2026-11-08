#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import AVFoundation
import Foundation

public enum PlaybackDRMPhase: String, Sendable {
    case requestStarted = "request_started"
    case spcBuilt = "spc_built"
    case ckcReceived = "ckc_received"
    case failed
}

/// Timing for one FairPlay step. The package never accepts SPC, CKC, or key bytes.
public struct PlaybackDRMNote: Sendable {
    public let phase: PlaybackDRMPhase
    public let persistent: Bool
    public let errorDomain: String
    public let errorCode: Int?
    public let durationMs: Double?

    public init(
        phase: PlaybackDRMPhase,
        persistent: Bool = false,
        errorDomain: String = "",
        errorCode: Int? = nil,
        durationMs: Double? = nil
    ) {
        self.phase = phase
        self.persistent = persistent
        self.errorDomain = String(errorDomain.prefix(80))
        self.errorCode = errorCode
        self.durationMs = durationMs
    }

    func fields() -> [String: JSONValue] {
        var row: [String: JSONValue] = [
            "phase": .string(phase.rawValue),
            "persistent": .bool(persistent),
            "error_domain": .string(errorDomain),
            "secrets_exposed": .bool(false)
        ]
        if let errorCode {
            row["error_code"] = .number(Double(errorCode))
        }
        if let durationMs, durationMs.isFinite {
            row["duration_ms"] = .number(durationMs)
        }
        return row
    }
}

/// Host observes `AVContentKeySession` and returns new notes only.
/// `playbackDRMDrain` must not call back into `PlaybackDebugService`.
public protocol PlaybackDRMProviding: AnyObject {
    func playbackDRMDrain() -> [PlaybackDRMNote]
}

struct PlaybackDigest {
    var status = ""
    var waitingReason = ""
    var stalled = false
    var external = false
    var lag: Double?
    var observed: Double?
    var indicated: Double?
    var width: Double?
    var height: Double?
    var dropped: Int?
    var errorDomain = ""
    var errorCode: Int?
}

/// Ring buffer of playback health events for the OTT timeline.
public final class PlaybackDebugService: @unchecked Sendable {
    public static let shared = PlaybackDebugService()

    static let maxEvents = 120
    static let maxDRM = 40
    static let maxOutbox = 40

    private let lock = NSLock()
    private var events: [[String: JSONValue]] = []
    private var drmEvents: [[String: JSONValue]] = []
    private var outbox: [DebugEvent] = []
    private weak var drmProvider: PlaybackDRMProviding?
    private var digest = PlaybackDigest()
    private var contentId = ""
    private var playbackId = ""
    private var attached = false
    private var explicitAttach = false
    private var lastTrack = Date.distantPast
    private var lastDigest = Date.distantPast
    private let binding = PlaybackPlayerBinding()

    private init() {}

    /// Observes this player until `detach` or another `attach`. Content ids are labels only.
    public func attach(player: AVPlayer, contentId: String = "", playbackId: String = "") {
        let content = Self.clip(contentId)
        let playback = Self.clip(playbackId)
        let work = {
            self.explicitAttach = true
            self.setIdentity(contentId: content, playbackId: playback)
            self.binding.bind(player, service: self)
            self.setAttached(true)
        }
        Self.onMain(work)
    }

    /// The observed player, for remote play/pause. Read on the main thread.
    var boundPlayer: AVPlayer? {
        binding.current
    }

    public func detach() {
        Self.onMain {
            self.explicitAttach = false
            self.binding.stop()
            self.setAttached(false)
        }
    }

    public func updateIdentity(contentId: String, playbackId: String) {
        setIdentity(contentId: Self.clip(contentId), playbackId: Self.clip(playbackId))
    }

    /// `started` true opens an ad break. The identifier is a label, not a tracking URL.
    public func noteAdBreak(started: Bool, identifier: String) {
        let kind = started ? "ad_break_start" : "ad_break_end"
        record(kind: kind, fields: ["ad_id": .string(Self.clip(identifier))])
    }

    public func noteDRM(_ note: PlaybackDRMNote) {
        rememberDRM(note.fields())
        record(kind: "drm", fields: note.fields())
    }

    public func registerDRMProvider(_ provider: PlaybackDRMProviding?) {
        lock.lock()
        drmProvider = provider
        lock.unlock()
    }

    func snapshot() -> [String: JSONValue] {
        ingestDRM()
        lock.lock()
        let payload = Self.makeSnapshot(
            digest: digest,
            events: events,
            contentId: contentId,
            playbackId: playbackId,
            attached: attached
        )
        lock.unlock()
        return payload
    }

    func drmSnapshot() -> [String: JSONValue] {
        ingestDRM()
        lock.lock()
        let copy = drmEvents
        let content = contentId
        let playback = playbackId
        lock.unlock()
        return [
            "events": .array(copy.reversed().map(JSONValue.object)),
            "count": .number(Double(copy.count)),
            "content_id": .string(content),
            "playback_id": .string(playback),
            "secrets_exposed": .bool(false)
        ]
    }

    func waterfallSnapshot() -> [String: JSONValue] {
        PlaybackWaterfall.shared.snapshot()
    }

    /// Scalar health for the OTT summary. One per second, with no timeline rows.
    func liveDigest(now: Date = Date()) -> [String: JSONValue]? {
        lock.lock()
        let due = attached && now.timeIntervalSince(lastDigest) >= 1
        if due {
            lastDigest = now
        }
        let payload = Self.digestPayload(
            digest: digest,
            contentId: contentId,
            playbackId: playbackId,
            attached: attached
        )
        lock.unlock()
        guard due else { return nil }
        return payload
    }

    func drainEvents() -> [DebugEvent] {
        ingestDRM()
        lock.lock()
        let copy = outbox
        outbox = []
        lock.unlock()
        return copy
    }

    @MainActor
    func trackVisiblePlayer(now: Date = Date()) {
        #if canImport(UIKit)
        guard now.timeIntervalSince(lastTrack) >= 1 else { return }
        lastTrack = now
        if explicitAttach, binding.current != nil {
            adoptActivityIdentity()
            return
        }
        explicitAttach = false
        guard let player = ActivityPlayerProbe.currentPlayer() else {
            if binding.current != nil {
                binding.stop()
                setAttached(false)
            }
            return
        }
        if binding.current !== player {
            binding.bind(player, service: self)
            setAttached(true)
        }
        adoptActivityIdentity()
        #endif
    }

    func resetForTests() {
        let clear = {
            self.binding.stop()
            self.lock.lock()
            self.events = []
            self.drmEvents = []
            self.outbox = []
            self.drmProvider = nil
            self.digest = PlaybackDigest()
            self.contentId = ""
            self.playbackId = ""
            self.attached = false
            self.explicitAttach = false
            self.lastTrack = .distantPast
            self.lastDigest = .distantPast
            self.lock.unlock()
            PlaybackWaterfall.shared.resetForTests()
        }
        if Thread.isMainThread {
            clear()
        } else {
            DispatchQueue.main.sync(execute: clear)
        }
    }

    func record(kind: String, fields: [String: JSONValue]) {
        var payload = fields
        payload["kind"] = .string(kind)
        payload["timestamp"] = .number(Date().timeIntervalSince1970)
        lock.lock()
        payload["content_id"] = .string(contentId)
        payload["playback_id"] = .string(playbackId)
        events.append(payload)
        Self.trim(&events, max: Self.maxEvents)
        outbox.append(DebugEvent(module: "playback", kind: "event", payload: payload))
        if outbox.count > Self.maxOutbox {
            outbox.removeFirst(outbox.count - Self.maxOutbox)
        }
        lock.unlock()
    }

    func update(_ apply: (inout PlaybackDigest) -> Void) {
        lock.lock()
        apply(&digest)
        lock.unlock()
    }

    private func setAttached(_ value: Bool) {
        lock.lock()
        attached = value
        lock.unlock()
    }

    private func setIdentity(contentId: String, playbackId: String) {
        lock.lock()
        let changed = self.contentId != contentId || self.playbackId != playbackId
        self.contentId = contentId
        self.playbackId = playbackId
        lock.unlock()
        if changed, !contentId.isEmpty || !playbackId.isEmpty {
            record(kind: "content", fields: [:])
        }
    }

    private func adoptActivityIdentity() {
        let host = ActivityDebugRegistry.snapshot()
        guard case let .string(value) = host["content_id"] else { return }
        let content = Self.clip(value)
        guard !content.isEmpty else { return }
        lock.lock()
        let same = contentId == content
        let playback = playbackId
        lock.unlock()
        if !same {
            setIdentity(contentId: content, playbackId: playback)
        }
    }

    private func rememberDRM(_ fields: [String: JSONValue]) {
        var row = fields
        row["timestamp"] = .number(Date().timeIntervalSince1970)
        lock.lock()
        row["content_id"] = .string(contentId)
        row["playback_id"] = .string(playbackId)
        drmEvents.append(row)
        Self.trim(&drmEvents, max: Self.maxDRM)
        lock.unlock()
    }

    private func ingestDRM() {
        lock.lock()
        let provider = drmProvider
        lock.unlock()
        guard let provider else { return }
        for note in provider.playbackDRMDrain() {
            noteDRM(note)
        }
    }

    private static func clip(_ value: String) -> String {
        String(value.prefix(128))
    }

    private static func trim(_ rows: inout [[String: JSONValue]], max: Int) {
        if rows.count > max {
            rows.removeFirst(rows.count - max)
        }
    }

    private static func onMain(_ work: @escaping () -> Void) {
        if Thread.isMainThread {
            work()
        } else {
            DispatchQueue.main.async(execute: work)
        }
    }
}

extension PlaybackDebugService {
    fileprivate static func makeSnapshot(
        digest: PlaybackDigest,
        events: [[String: JSONValue]],
        contentId: String,
        playbackId: String,
        attached: Bool
    ) -> [String: JSONValue] {
        var payload = digestPayload(
            digest: digest,
            contentId: contentId,
            playbackId: playbackId,
            attached: attached
        )
        payload["events"] = .array(events.reversed().map(JSONValue.object))
        payload["count"] = .number(Double(events.count))
        return payload
    }

    private static func digestPayload(
        digest: PlaybackDigest,
        contentId: String,
        playbackId: String,
        attached: Bool
    ) -> [String: JSONValue] {
        [
            "attached": .bool(attached),
            "content_id": .string(contentId),
            "playback_id": .string(playbackId),
            "status": .string(digest.status),
            "waiting_reason": .string(digest.waitingReason),
            "stalled": .bool(digest.stalled),
            "external_playback": .bool(digest.external),
            "live_edge_lag_sec": Self.number(digest.lag),
            "observed_bps": Self.number(digest.observed),
            "indicated_bps": Self.number(digest.indicated),
            "presentation_width": Self.number(digest.width),
            "presentation_height": Self.number(digest.height),
            "dropped_frames": digest.dropped.map { JSONValue.number(Double($0)) } ?? .null,
            "error_domain": .string(digest.errorDomain),
            "error_code": digest.errorCode.map { JSONValue.number(Double($0)) } ?? .null,
            "secrets_exposed": .bool(false)
        ]
    }

    private static func number(_ value: Double?) -> JSONValue {
        guard let value, value.isFinite else { return .null }
        return .number(value)
    }
}
#endif
