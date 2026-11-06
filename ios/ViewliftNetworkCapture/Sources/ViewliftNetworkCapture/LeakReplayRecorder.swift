#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Rolling low-resolution screen recording used to replay the seconds before a leak.
/// Frames are small JPEGs with the touches that happened since the previous frame.
final class LeakReplayRecorder: @unchecked Sendable {
    static let shared = LeakReplayRecorder()

    static let frameInterval: TimeInterval = 0.5
    static let maxFrames = 24
    static let longestSide: CGFloat = 400
    static let jpegQuality: CGFloat = 0.45

    struct Touch {
        let phase: String
        let pointX: Double
        let pointY: Double
    }

    struct Frame {
        let time: TimeInterval
        let jpeg: Data
        let width: Int
        let height: Int
        let screen: String
        let touches: [Touch]
    }

    private let lock = NSLock()
    private var frames: [Frame] = []
    private var pendingTouches: [Touch] = []
    private var recording = false
    #if canImport(UIKit)
    private var timer: Timer?
    #endif

    private init() {}

    var isRecording: Bool {
        lock.lock()
        defer { lock.unlock() }
        return recording
    }

    func start() {
        lock.lock()
        guard !recording else {
            lock.unlock()
            return
        }
        recording = true
        lock.unlock()
        #if canImport(UIKit)
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let timer = Timer(timeInterval: Self.frameInterval, repeats: true) { [weak self] _ in
                self?.captureFrame()
            }
            RunLoop.main.add(timer, forMode: .common)
            self.timer = timer
        }
        #endif
    }

    func stop() {
        lock.lock()
        recording = false
        frames.removeAll()
        pendingTouches.removeAll()
        lock.unlock()
        #if canImport(UIKit)
        DispatchQueue.main.async { [weak self] in
            self?.timer?.invalidate()
            self?.timer = nil
        }
        #endif
    }

    /// Records a touch in window coordinates normalized to 0...1.
    func noteTouch(phase: String, pointX: Double, pointY: Double) {
        lock.lock()
        defer { lock.unlock() }
        guard recording else { return }
        pendingTouches.append(Touch(phase: phase, pointX: pointX, pointY: pointY))
        if pendingTouches.count > 40 {
            pendingTouches.removeFirst(pendingTouches.count - 40)
        }
    }

    func append(_ frame: Frame) {
        lock.lock()
        frames.append(frame)
        if frames.count > Self.maxFrames {
            frames.removeFirst(frames.count - Self.maxFrames)
        }
        lock.unlock()
    }

    /// The newest frames that fit in `byteBudget`, oldest first, as a replay clip.
    func clip(before date: Date, seconds: TimeInterval, byteBudget: Int) -> [String: JSONValue]? {
        lock.lock()
        let copy = frames
        lock.unlock()
        let now = date.timeIntervalSince1970
        var picked: [Frame] = []
        var used = 0
        for frame in copy.reversed() where now - frame.time <= seconds {
            let cost = (frame.jpeg.count * 4) / 3 + 128
            guard used + cost <= byteBudget else { break }
            used += cost
            picked.append(frame)
        }
        guard !picked.isEmpty else { return nil }
        let ordered = picked.reversed().map { Self.describe($0, now: now) }
        return [
            "mime": .string("image/jpeg"),
            "frame_interval_ms": .number(Self.frameInterval * 1_000),
            "frames": .array(ordered)
        ]
    }

    private static func describe(_ frame: Frame, now: TimeInterval) -> JSONValue {
        .object([
            "ago_ms": .number(max(0, (now - frame.time) * 1_000)),
            "width": .number(Double(frame.width)),
            "height": .number(Double(frame.height)),
            "screen_class": .string(frame.screen),
            "base64": .string(frame.jpeg.base64EncodedString()),
            "touches": .array(frame.touches.map { touch in
                .object([
                    "phase": .string(touch.phase),
                    "x": .number(touch.pointX),
                    "y": .number(touch.pointY)
                ])
            })
        ])
    }

    private func takeTouches() -> [Touch] {
        lock.lock()
        defer { lock.unlock() }
        let touches = pendingTouches
        pendingTouches.removeAll()
        return touches
    }

    #if canImport(UIKit)
    private func captureFrame() {
        guard isRecording, UIApplication.shared.applicationState == .active,
              let window = HangScreen.keyWindow(), window.bounds.width > 0, window.bounds.height > 0 else {
            return
        }
        let bounds = window.bounds
        let scale = min(1, Self.longestSide / max(bounds.width, bounds.height))
        let size = CGSize(width: floor(bounds.width * scale), height: floor(bounds.height * scale))
        let format = UIGraphicsImageRendererFormat.preferred()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            window.drawHierarchy(in: CGRect(origin: .zero, size: size), afterScreenUpdates: false)
        }
        guard let jpeg = image.jpegData(compressionQuality: Self.jpegQuality) else { return }
        append(Frame(
            time: Date().timeIntervalSince1970,
            jpeg: jpeg,
            width: Int(size.width),
            height: Int(size.height),
            screen: HangScreen.current().className,
            touches: takeTouches()
        ))
    }
    #endif
}
#endif
