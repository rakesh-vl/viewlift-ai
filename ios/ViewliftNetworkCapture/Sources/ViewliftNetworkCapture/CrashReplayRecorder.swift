#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Keeps the last ten seconds of the screen on disk while a debug build runs, so a crash has a replay.
/// Frames are small JPEGs taken four times a second, with the touches since the previous frame.
final class CrashReplayRecorder: @unchecked Sendable {
    static let shared = CrashReplayRecorder()

    static let frameInterval: TimeInterval = 0.25
    static let longestSide: CGFloat = 480
    static let jpegQuality: CGFloat = 0.4
    static let maxPendingTouches = 40

    private let lock = NSLock()
    private let io = DispatchQueue(label: "com.viewlift.networkcapture.crash-replay", qos: .utility)
    private var ring: CrashReplayRing?
    private var pendingTouches: [CrashReplayTouch] = []
    #if canImport(UIKit)
    private var timer: Timer?
    #endif

    private init() {}

    var isRecording: Bool {
        lock.lock()
        defer { lock.unlock() }
        return ring != nil
    }

    /// Starts a fresh ring in `directory`. Anything already there is deleted, so freeze a crash's frames first.
    func start(directory: URL) {
        lock.lock()
        guard ring == nil else {
            lock.unlock()
            return
        }
        ring = CrashReplayRing(directory: directory)
        lock.unlock()
        #if canImport(UIKit)
        DispatchQueue.main.async { [weak self] in
            guard let self, self.timer == nil else { return }
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
        ring = nil
        pendingTouches.removeAll()
        lock.unlock()
        #if canImport(UIKit)
        DispatchQueue.main.async { [weak self] in
            self?.timer?.invalidate()
            self?.timer = nil
        }
        #endif
    }

    /// Waits for frames already queued for disk. The crash path calls this before it moves the ring.
    func flush() {
        io.sync {}
    }

    func noteTouch(_ touch: CrashReplayTouch) {
        lock.lock()
        defer { lock.unlock() }
        guard ring != nil else { return }
        pendingTouches.append(touch)
        if pendingTouches.count > Self.maxPendingTouches {
            pendingTouches.removeFirst(pendingTouches.count - Self.maxPendingTouches)
        }
    }

    /// tvOS has no touches. A remote press is drawn as a caption on the next frame.
    func notePress(_ name: String) {
        noteTouch(CrashReplayTouch(phase: "press", pointX: 0.5, pointY: 0.5, label: name))
    }

    private func takeTouches() -> [CrashReplayTouch] {
        lock.lock()
        defer { lock.unlock() }
        let touches = pendingTouches
        pendingTouches.removeAll()
        return touches
    }

    private func currentRing() -> CrashReplayRing? {
        lock.lock()
        defer { lock.unlock() }
        return ring
    }

    #if canImport(UIKit)
    private func captureFrame() {
        guard let ring = currentRing(), UIApplication.shared.applicationState == .active,
              let window = HangScreen.keyWindow(), window.bounds.width > 1, window.bounds.height > 1 else {
            return
        }
        let bounds = window.bounds
        let scale = min(1, Self.longestSide / max(bounds.width, bounds.height))
        guard let image = WindowSnapshot.image(of: window, scale: scale, prepare: false) else { return }
        let screen = HangScreen.current()
        CrashContextWriter.shared.noteScreen(className: screen.className, title: screen.title)
        let frame = CrashReplaySlot(
            slot: 0,
            time: Date().timeIntervalSince1970,
            width: Int(safe: bounds.width * scale),
            height: Int(safe: bounds.height * scale),
            screen: screen.className,
            title: screen.title,
            touches: takeTouches()
        )
        io.async {
            guard let jpeg = image.jpegData(compressionQuality: Self.jpegQuality) else { return }
            ring.append(jpeg: jpeg, frame: frame)
        }
    }
    #endif
}
#endif
