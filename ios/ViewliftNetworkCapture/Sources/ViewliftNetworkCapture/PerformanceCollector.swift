#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

final class PerformanceCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var lastCPUTime: TimeInterval = 0
    private var lastWallTime = ProcessInfo.processInfo.systemUptime
    private var peakMemoryBytes: UInt64 = 0
    private var currentFPS: Double = 0
    private var hangCount = 0
    private var lastHangDuration: TimeInterval = 0
    private var watchdogTask: Task<Void, Never>?

    #if canImport(UIKit)
    private var frameCounter: FrameCounter?
    #endif

    func start() {
        #if canImport(UIKit)
        if Thread.isMainThread {
            HangMainThread.remember()
        }
        Task { @MainActor in
            HangMainThread.remember()
            let counter = FrameCounter { [weak self] fps in
                self?.setFPS(fps)
            }
            counter.start()
            self.frameCounter = counter
        }
        #endif
        watchdogTask = Task { [weak self] in
            await self?.watchMainThread()
        }
    }

    func stop() {
        #if canImport(UIKit)
        Task { @MainActor in
            self.frameCounter?.stop()
            self.frameCounter = nil
        }
        #endif
        watchdogTask?.cancel()
        watchdogTask = nil
    }

    func sample() -> [String: JSONValue] {
        let memory = residentMemoryBytes()
        lock.lock()
        peakMemoryBytes = max(peakMemoryBytes, memory)
        let peak = peakMemoryBytes
        let now = ProcessInfo.processInfo.systemUptime
        let cpuTime = processCPUTime()
        let wallDelta = max(0.001, now - lastWallTime)
        let cpu = max(0, min(100 * Double(ProcessInfo.processInfo.activeProcessorCount),
                             ((cpuTime - lastCPUTime) / wallDelta) * 100))
        lastCPUTime = cpuTime
        lastWallTime = now
        lock.unlock()

        var payload: [String: JSONValue] = [
            "timestamp": .number(Date().timeIntervalSince1970),
            "cpu_percent": .number(cpu),
            "memory_bytes": .number(Double(memory)),
            "peak_memory_bytes": .number(Double(peak)),
            "thermal_state": .string(thermalState())
        ]
        lock.lock()
        payload["hang_count"] = .number(Double(hangCount))
        payload["last_hang_ms"] = .number(lastHangDuration * 1_000)
        lock.unlock()
        #if canImport(UIKit)
        lock.lock()
        let fps = currentFPS
        lock.unlock()
        payload["fps"] = .number(fps)
        #else
        payload["fps"] = .number(0)
        #endif
        return payload
    }

    private func setFPS(_ fps: Double) {
        lock.lock()
        currentFPS = fps
        lock.unlock()
    }

    private func watchMainThread() async {
        while !Task.isCancelled {
            let outcome = await waitForMain()
            if outcome.delay > 0.4 {
                await recordHang(outcome.delay, symbols: outcome.symbols)
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
    }

    /// Polls until the main queue runs the probe. Samples the blocked stack once, past 400 ms.
    private func waitForMain() async -> (delay: TimeInterval, symbols: [String]) {
        let started = ProcessInfo.processInfo.systemUptime
        let gate = HangWait()
        var symbols: [String] = []
        DispatchQueue.main.async { gate.finish() }
        while !gate.isFinished, !Task.isCancelled {
            let delay = ProcessInfo.processInfo.systemUptime - started
            if delay > 0.4, symbols.isEmpty {
                symbols = HangMainThread.sampleSymbols()
            }
            try? await Task.sleep(nanoseconds: 40_000_000)
        }
        return (ProcessInfo.processInfo.systemUptime - started, symbols)
    }

    private func recordHang(_ duration: TimeInterval, symbols: [String]) async {
        bumpHang(duration)
        let event = await MainActor.run {
            HangReport.make(duration: duration, symbols: symbols)
        }
        CrashHangRecorder.shared.storeHang(event)
    }

    private func bumpHang(_ duration: TimeInterval) {
        lock.lock()
        hangCount += 1
        lastHangDuration = duration
        lock.unlock()
    }

    private func residentMemoryBytes() -> UInt64 {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(
            MemoryLayout<mach_task_basic_info>.size / MemoryLayout<natural_t>.size
        )
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? UInt64(info.resident_size) : 0
    }

    private func processCPUTime() -> TimeInterval {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return 0 }
        let user = Double(usage.ru_utime.tv_sec) + Double(usage.ru_utime.tv_usec) / 1_000_000
        let system = Double(usage.ru_stime.tv_sec) + Double(usage.ru_stime.tv_usec) / 1_000_000
        return user + system
    }

    private func thermalState() -> String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "unknown"
        }
    }
}

private final class HangWait: @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false

    func finish() {
        lock.lock()
        finished = true
        lock.unlock()
    }

    var isFinished: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished
    }
}

#if canImport(UIKit)
@MainActor
private final class FrameCounter {
    private let onFPS: (Double) -> Void
    private var displayLink: CADisplayLink?
    private var frames = 0
    private var windowStart = CACurrentMediaTime()
    init(onFPS: @escaping (Double) -> Void) {
        self.onFPS = onFPS
    }

    func start() {
        guard displayLink == nil else { return }
        let link = CADisplayLink(target: self, selector: #selector(tick))
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    func stop() {
        displayLink?.invalidate()
        displayLink = nil
    }

    @objc private func tick() {
        frames += 1
        let now = CACurrentMediaTime()
        let elapsed = now - windowStart
        if elapsed >= 1 {
            onFPS(Double(frames) / elapsed)
            frames = 0
            windowStart = now
        }
    }
}
#endif
#endif
