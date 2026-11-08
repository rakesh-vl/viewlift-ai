#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import AVFoundation
import UIKit
#endif

/// Optional host playback fields for the Live tab.
/// Only `title`, `content_id`, `state`, `is_live`, `position_seconds`, and `rate` are copied.
/// Stream URLs and tokens are ignored. The package stays free of AppCMS types.
public protocol ActivityDebugProviding: AnyObject {
    func activityDebugSnapshot() -> [String: JSONValue]
}

/// Registers the host provider used by `activity.snapshot` and the live feed.
public enum ActivityDebugRegistry {
    private static let lock = NSLock()
    private static weak var provider: ActivityDebugProviding?

    public static func register(_ provider: ActivityDebugProviding) {
        lock.lock()
        self.provider = provider
        lock.unlock()
    }

    public static func clear() {
        lock.lock()
        provider = nil
        lock.unlock()
    }

    static func snapshot() -> [String: JSONValue] {
        lock.lock()
        let active = provider
        lock.unlock()
        return active?.activityDebugSnapshot() ?? [:]
    }
}

enum ActivityTimeControl: String {
    case playing
    case paused
    case buffering
}

struct ActivityPlaybackSample: Equatable {
    var state: String
    var isLive: Bool
    var positionSeconds: Double?
    var rate: Double

    static let none = ActivityPlaybackSample(
        state: "none",
        isLive: false,
        positionSeconds: nil,
        rate: 0
    )
}

struct ActivitySampleMeta {
    var host: [String: JSONValue]
    var capturedAt: TimeInterval
    var tracking: Bool
}

struct ActivitySampleContext {
    var screenClass: String
    var screenTitle: String
    var lifecycle: String
    var playback: ActivityPlaybackSample
    var meta: ActivitySampleMeta
}

enum ActivityPayload {
    static let hostKeys = ["title", "content_id", "state", "is_live", "position_seconds", "rate"]

    static func make(_ context: ActivitySampleContext) -> [String: JSONValue] {
        [
            "screen_class": .string(context.screenClass),
            "screen_title": .string(context.screenTitle),
            "lifecycle": .string(context.lifecycle),
            "playback": .object(playbackObject(context.playback, host: context.meta.host)),
            "captured_at": .number(context.meta.capturedAt),
            "tracking": .bool(context.meta.tracking)
        ]
    }

    private static func playbackObject(
        _ playback: ActivityPlaybackSample,
        host: [String: JSONValue]
    ) -> [String: JSONValue] {
        var play: [String: JSONValue] = [
            "state": .string(playback.state),
            "is_live": .bool(playback.isLive),
            "rate": .number(playback.rate)
        ]
        if let position = playback.positionSeconds {
            play["position_seconds"] = .number(position)
        }
        for key in hostKeys {
            if let value = host[key], value != .null {
                play[key] = value
            }
        }
        return play
    }
}

enum ActivityPlayerProbe {
    static func describe(
        control: ActivityTimeControl?,
        durationIndefinite: Bool,
        positionSeconds: Double?,
        rate: Double
    ) -> ActivityPlaybackSample {
        guard let control else { return .none }
        let position = positionSeconds.flatMap { $0.isFinite ? $0 : nil }
        return ActivityPlaybackSample(
            state: control.rawValue,
            isLive: durationIndefinite,
            positionSeconds: position,
            rate: rate
        )
    }

    #if canImport(UIKit)
    /// Reads time control, rate, current time, and indefinite duration. Never reads `asset.url`.
    static func describe(player: AVPlayer) -> ActivityPlaybackSample {
        describe(
            control: control(player.timeControlStatus),
            durationIndefinite: player.currentItem?.duration.isIndefinite == true,
            positionSeconds: CMTimeGetSeconds(player.currentTime()),
            rate: Double(player.rate)
        )
    }

    static func sampleWindow() -> ActivityPlaybackSample {
        guard let player = currentPlayer() else { return .none }
        return describe(player: player)
    }

    static func currentPlayer() -> AVPlayer? {
        playerInKeyWindow()
    }

    private static func control(_ status: AVPlayer.TimeControlStatus) -> ActivityTimeControl {
        switch status {
        case .playing:
            return .playing
        case .waitingToPlayAtSpecifiedRate:
            return .buffering
        case .paused:
            return .paused
        @unknown default:
            return .paused
        }
    }

    private static func playerInKeyWindow() -> AVPlayer? {
        guard let root = HangScreen.keyWindow()?.layer else { return nil }
        var queue = [root]
        var index = 0
        var fallback: AVPlayer?
        while index < queue.count, index < 400 {
            let layer = queue[index]
            index += 1
            if let playerLayer = layer as? AVPlayerLayer, let player = playerLayer.player {
                if player.timeControlStatus == .playing {
                    return player
                }
                if fallback == nil {
                    fallback = player
                }
            }
            guard queue.count < 400, let children = layer.sublayers else { continue }
            queue.append(contentsOf: children.prefix(400 - queue.count))
        }
        return fallback
    }
    #endif
}

/// Samples the top screen and playback once a second while the Live tab is open.
final class ActivityDebugService: @unchecked Sendable {
    static let shared = ActivityDebugService()
    static let liveInterval: TimeInterval = 1
    /// About three previews a second. Each one is a small JPEG, so this does not wait on a full PNG.
    static let frameInterval: TimeInterval = 0.3

    private let lock = NSLock()
    private var tracking = false
    private var previewing = false
    private var latest: [String: JSONValue] = [:]
    private var lastSent = Date.distantPast
    private var lastFrame = Date.distantPast
    private var timer: DispatchSourceTimer?

    private init() {}

    @MainActor
    func snapshot() -> [String: JSONValue] {
        let payload = Self.capture(tracking: isTracking)
        store(payload)
        return payload
    }

    @MainActor
    func liveState(enabled: Bool, preview: Bool = false) -> [String: JSONValue] {
        if enabled {
            beginTracking()
            setPreviewing(preview)
        } else {
            endTracking()
        }
        var payload = Self.capture(tracking: enabled)
        store(payload)
        payload["live"] = .bool(enabled)
        payload["preview"] = .bool(enabled && preview)
        return payload
    }

    var isPreviewing: Bool {
        lock.lock()
        defer { lock.unlock() }
        return previewing
    }

    /// One JPEG when the dashboard asked for a preview and the last frame is old enough.
    @MainActor
    func takeFrameIfDue(now: Date = Date()) -> DebugEvent? {
        guard claimFrame(now) else { return nil }
        #if canImport(UIKit)
        guard let payload = PreviewCapture.capture() else { return nil }
        return DebugEvent(module: "activity", kind: "frame", payload: payload)
        #else
        return nil
        #endif
    }

    func drainEvents(now: Date = Date()) -> [DebugEvent] {
        lock.lock()
        defer { lock.unlock() }
        guard tracking, !latest.isEmpty, now.timeIntervalSince(lastSent) >= Self.liveInterval else {
            return []
        }
        lastSent = now
        return [DebugEvent(module: "activity", kind: "live", payload: latest)]
    }

    private func beginTracking() {
        lock.lock()
        let existing = timer
        let running = tracking && existing != nil
        tracking = true
        if !running {
            lastSent = .distantPast
        }
        lock.unlock()
        guard !running else { return }
        existing?.cancel()
        let source = DispatchSource.makeTimerSource(queue: .main)
        source.schedule(deadline: .now() + Self.liveInterval, repeating: Self.liveInterval)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated {
                self?.refreshSample()
            }
        }
        lock.lock()
        let still = tracking
        if still {
            timer = source
        }
        lock.unlock()
        if still {
            source.resume()
        } else {
            source.cancel()
        }
    }

    private func endTracking() {
        lock.lock()
        tracking = false
        previewing = false
        let active = timer
        timer = nil
        latest = [:]
        lock.unlock()
        active?.cancel()
    }

    private func setPreviewing(_ enabled: Bool) {
        lock.lock()
        let turnedOn = enabled && !previewing
        previewing = enabled
        if turnedOn {
            lastFrame = .distantPast
        }
        lock.unlock()
    }

    private func claimFrame(_ now: Date) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard previewing, now.timeIntervalSince(lastFrame) >= Self.frameInterval else {
            return false
        }
        lastFrame = now
        return true
    }

    @MainActor
    private func refreshSample() {
        guard isTracking else { return }
        store(Self.capture(tracking: true))
    }

    private var isTracking: Bool {
        lock.lock()
        defer { lock.unlock() }
        return tracking
    }

    private func store(_ payload: [String: JSONValue]) {
        lock.lock()
        latest = payload
        lock.unlock()
    }

    @MainActor
    private static func capture(tracking: Bool) -> [String: JSONValue] {
        let screen = HangScreen.current()
        #if canImport(UIKit)
        let playback = ActivityPlayerProbe.sampleWindow()
        let lifecycle = lifecycleName()
        #else
        let playback = ActivityPlaybackSample.none
        let lifecycle = ""
        #endif
        return ActivityPayload.make(ActivitySampleContext(
            screenClass: screen.className,
            screenTitle: screen.title,
            lifecycle: lifecycle,
            playback: playback,
            meta: ActivitySampleMeta(
                host: ActivityDebugRegistry.snapshot(),
                capturedAt: Date().timeIntervalSince1970,
                tracking: tracking
            )
        ))
    }

    #if canImport(UIKit)
    private static func lifecycleName() -> String {
        switch UIApplication.shared.applicationState {
        case .active:
            return "foreground"
        case .background:
            return "background"
        case .inactive:
            return "inactive"
        @unknown default:
            return "foreground"
        }
    }
    #endif
}
#endif
