#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// What the app was doing just before a crash: screens, taps, presses, requests, console, and the
/// environment. Written to `context.json` once a second so a crash that kills the process leaves it behind.
final class CrashContextWriter: @unchecked Sendable {
    static let shared = CrashContextWriter()

    static let writeInterval: TimeInterval = 1
    static let maxScreens = 10
    static let maxRequests = 10
    static let maxPresses = 20
    static let consoleSeconds: TimeInterval = 15

    private let lock = NSLock()
    private let queue = DispatchQueue(label: "com.viewlift.networkcapture.crash-context", qos: .utility)
    private var timer: DispatchSourceTimer?
    private var directory: URL?
    private var screens: [[String: JSONValue]] = []
    private var requests: [[String: JSONValue]] = []
    private var presses: [[String: JSONValue]] = []
    private var performance: [String: JSONValue] = [:]
    private var environment: [String: JSONValue] = [:]
    private var appState = "unknown"

    private init() {}

    func start(directory: URL) {
        lock.lock()
        self.directory = directory
        let running = timer != nil
        lock.unlock()
        guard !running else { return }
        captureEnvironment()
        let source = DispatchSource.makeTimerSource(queue: queue)
        source.schedule(deadline: .now() + Self.writeInterval, repeating: Self.writeInterval)
        source.setEventHandler { [weak self] in
            self?.refreshAppState()
            self?.write()
        }
        lock.lock()
        timer = source
        lock.unlock()
        source.resume()
    }

    func stop() {
        lock.lock()
        let source = timer
        timer = nil
        directory = nil
        lock.unlock()
        source?.cancel()
    }

    func noteScreen(className: String, title: String) {
        guard !className.isEmpty else { return }
        lock.lock()
        defer { lock.unlock() }
        if case let .string(last)? = screens.last?["screen_class"], last == className { return }
        screens.append([
            "time": .number(Date().timeIntervalSince1970),
            "screen_class": .string(className),
            "title": .string(title)
        ])
        if screens.count > Self.maxScreens {
            screens.removeFirst(screens.count - Self.maxScreens)
        }
    }

    func notePress(_ name: String) {
        lock.lock()
        defer { lock.unlock() }
        presses.append(["time": .number(Date().timeIntervalSince1970), "button": .string(name)])
        if presses.count > Self.maxPresses {
            presses.removeFirst(presses.count - Self.maxPresses)
        }
    }

    /// Method, host, path, and status only. Headers, query strings, and bodies never reach the crash file.
    func noteRequest(_ exchange: CapturedHTTPExchange) {
        var row: [String: JSONValue] = [
            "time": .number(Date().timeIntervalSince1970),
            "method": .string(exchange.method),
            "host": .string(exchange.host),
            "path": .string(String(exchange.path.split(separator: "?").first ?? "")),
            "status": exchange.status.map { .number(Double($0)) } ?? .null,
            "duration_ms": exchange.durationMs.map(JSONValue.number) ?? .null
        ]
        if let error = exchange.error, !error.isEmpty {
            row["error"] = .string(String(error.prefix(200)))
        }
        lock.lock()
        defer { lock.unlock() }
        requests.append(row)
        if requests.count > Self.maxRequests {
            requests.removeFirst(requests.count - Self.maxRequests)
        }
    }

    func notePerformance(_ sample: [String: JSONValue]) {
        lock.lock()
        performance = sample
        lock.unlock()
    }

    /// Writes immediately. The uncaught exception path calls this before it freezes the replay.
    func writeNow() {
        queue.sync { write() }
    }

    func snapshot(now: Date = Date()) -> [String: JSONValue] {
        lock.lock()
        var context: [String: JSONValue] = [
            "captured_at": .number(now.timeIntervalSince1970),
            "screens": .array(screens.map(JSONValue.object)),
            "requests": .array(requests.map(JSONValue.object)),
            "presses": .array(presses.map(JSONValue.object)),
            "performance": .object(performance),
            "environment": .object(environment.merging(["app_state": .string(appState)]) { _, new in new })
        ]
        lock.unlock()
        context["actions"] = .array(HangInteractionTrail.shared.recent(before: now, limit: 24))
        context["console"] = .array(HangConsoleBuffer.shared.recent(before: now, seconds: Self.consoleSeconds))
        return context
    }

    private func write() {
        lock.lock()
        let target = directory
        lock.unlock()
        guard let target, let data = try? JSONEncoder().encode(snapshot()) else { return }
        try? FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
        try? data.write(to: target.appendingPathComponent(CrashReplayRing.contextName), options: .atomic)
    }

    private func captureEnvironment() {
        let identity = AppIdentity.current(frameworks: [])
        let process = ProcessInfo.processInfo
        let values: [String: JSONValue] = [
            "app_name": .string(identity.appName),
            "bundle_id": .string(identity.bundleId),
            "app_version": .string(identity.appVersion),
            "build": .string(identity.build),
            "os_version": .string(DeviceIdentity.osVersion()),
            "locale": .string(Locale.current.identifier),
            "processors": .number(Double(process.activeProcessorCount)),
            "physical_memory_bytes": .number(Double(process.physicalMemory)),
            "launched_at": .number(Date().timeIntervalSince1970)
        ]
        lock.lock()
        environment = values
        lock.unlock()
        DispatchQueue.main.async { [weak self] in
            let device = MainActor.assumeIsolated { DeviceIdentity.current() }
            let hardware: [String: JSONValue] = [
                "device_model": .string(device.deviceModel),
                "device_name": .string(device.deviceName),
                "is_simulator": .bool(device.isSimulator)
            ]
            self?.lock.lock()
            self?.environment.merge(hardware) { _, new in new }
            self?.lock.unlock()
        }
    }

    private func refreshAppState() {
        let process = ProcessInfo.processInfo
        let thermal: String
        switch process.thermalState {
        case .nominal: thermal = "nominal"
        case .fair: thermal = "fair"
        case .serious: thermal = "serious"
        case .critical: thermal = "critical"
        @unknown default: thermal = "unknown"
        }
        lock.lock()
        environment["thermal_state"] = .string(thermal)
        #if os(iOS)
        environment["low_power_mode"] = .bool(process.isLowPowerModeEnabled)
        #endif
        lock.unlock()
        #if canImport(UIKit)
        DispatchQueue.main.async { [weak self] in
            let state = MainActor.assumeIsolated { Self.applicationState() }
            self?.lock.lock()
            self?.appState = state
            self?.lock.unlock()
        }
        #endif
    }

    #if canImport(UIKit)
    @MainActor
    private static func applicationState() -> String {
        switch AutomationScreen.application?.applicationState {
        case .active?: return "active"
        case .inactive?: return "inactive"
        case .background?: return "background"
        default: return "unknown"
        }
    }
    #endif
}
#endif
