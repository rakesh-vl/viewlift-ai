#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import OSLog
import ViewliftCaptureObjC

#if canImport(UIKit)
import UIKit
#endif

public final class DebugBridgeService: @unchecked Sendable {
    public static let shared = DebugBridgeService()

    private static let log = Logger(
        subsystem: "com.viewlift.networkcapture",
        category: "debug-bridge"
    )

    let performance = PerformanceCollector()
    private lazy var executor = DebugCommandExecutor(performance: performance)
    private let lock = NSLock()
    private var task: Task<Void, Never>?
    private var transport: DebugTransporting?
    private var configuration: CaptureConfiguration?
    private var lifecycleMonitor: ConnectionLifecycleMonitor?
    private var wakeReason: String?
    private var needsRegistration = false
    let clientId: String

    private init(identity: ClientIdentityProviding = UserDefaultsClientIdentity.shared) {
        clientId = identity.clientId
    }

    public func start(
        configuration: CaptureConfiguration,
        transport: DebugTransporting
    ) {
        guard !configuration.debugModules.isEmpty else { return }
        stop()
        let effective = RemoteSettingsService.shared.bind(base: configuration) { [weak self] updated in
            self?.apply(updated)
        }
        lock.lock()
        self.transport = transport
        self.configuration = effective
        lock.unlock()
        performance.start()
        MemoryDebugService.shared.installLifecycleTracking()
        MacroRecorder.shared.install()
        CrashHangRecorder.shared.install()
        Self.applyCrashReplay(effective)
        if effective.debugModules.contains(.ui) {
            SwiftUIDebugTree.enableRecording()
            FocusDebugMonitor.shared.install()
        }
        task = Task { [weak self] in
            await self?.run()
        }
        let monitor = ConnectionLifecycleMonitor { [weak self] event in
            self?.handle(event)
        }
        monitor.start()
        lock.lock()
        lifecycleMonitor = monitor
        lock.unlock()
    }

    public func stop() {
        lock.lock()
        let active = task
        let monitor = lifecycleMonitor
        task = nil
        lifecycleMonitor = nil
        wakeReason = nil
        transport = nil
        configuration = nil
        needsRegistration = false
        lock.unlock()
        active?.cancel()
        monitor?.stop()
        performance.stop()
        CrashReplayRecorder.shared.stop()
        CrashContextWriter.shared.stop()
        ObjectHandleRegistry.shared.removeAll()
    }

    private func run() async {
        var registered = false
        var resumeReason: String?
        var lastPerformance = Date.distantPast
        while !Task.isCancelled {
            guard let transport = currentTransport(), let config = currentConfiguration() else {
                return
            }
            if let reason = takeWakeReason() {
                registered = false
                resumeReason = reason
            }
            if takeNeedsRegistration() {
                registered = false
            }
            do {
                if !registered {
                    let device = await MainActor.run { DeviceIdentity.current() }
                    // Frameworks loaded since launch get a path in the crash record's image table.
                    VLCCrashSignalsRefreshImagePaths()
                    try await transport.register(client: registration(config, device: device))
                    registered = true
                    Self.log.info("Remote debug bridge registered")
                    if let reason = resumeReason {
                        resumeReason = nil
                        await announceResume(reason: reason, transport: transport)
                    }
                }
                lastPerformance = try await sendPerformanceSample(
                    config: config, transport: transport, last: lastPerformance
                )
                try await sendCrashEvents(transport: transport)
                try await sendHangEvents(transport: transport)
                try await CrashReplayUploader.shared.sendPending(transport: transport, clientId: clientId)
                try await sendMemoryEvents(config: config, transport: transport)
                try await sendActivityEvents(config: config, transport: transport)
                try await sendPlaybackEvents(config: config, transport: transport)
                try await sendFocusEvents(config: config, transport: transport)
                let commands = try await transport.pollCommands(clientId: clientId)
                for command in commands where command.expiresAt > Date().timeIntervalSince1970 {
                    await execute(command, transport: transport, config: config)
                }
                // A live preview has to leave the device faster than the idle command poll.
                let pause: UInt64 = ActivityDebugService.shared.isPreviewing ? 250 : 500
                await interruptibleSleep(milliseconds: pause)
            } catch let error as TransportError where error.isWaitingForSession {
                // Transport is still starting or re-pairing. Check again shortly instead of backing off.
                registered = false
                await interruptibleSleep(milliseconds: 250)
            } catch {
                registered = false
                Self.log.error(
                    "Debug bridge connection failed: \(String(describing: error), privacy: .public)"
                )
                await interruptibleSleep(milliseconds: 2_000)
            }
        }
    }

    private func execute(
        _ command: DebugCommand,
        transport: DebugTransporting,
        config: CaptureConfiguration
    ) async {
        let module = command.name.split(separator: ".").first.map(String.init) ?? "debug"
        do {
            let payload = try await executor.execute(command, config: config)
            try await transport.send(
                event: DebugEvent(
                    module: module,
                    kind: "command-result",
                    payload: [
                        "command_id": .string(command.id),
                        "name": .string(command.name),
                        "ok": .bool(true),
                        "result": .object(payload)
                    ]
                ),
                clientId: clientId
            )
        } catch {
            try? await transport.send(
                event: DebugEvent(
                    module: module,
                    kind: "command-result",
                    payload: [
                        "command_id": .string(command.id),
                        "name": .string(command.name),
                        "ok": .bool(false),
                        "error": .string(String(describing: error))
                    ]
                ),
                clientId: clientId
            )
        }
    }

    private func registration(
        _ config: CaptureConfiguration,
        device: DeviceIdentity
    ) -> DebugClientRegistration {
        let identity = AppIdentity.current()
        #if os(tvOS)
        let platform = "tvOS"
        #elseif os(iOS)
        let platform = "iOS"
        #else
        let platform = "macOS"
        #endif
        return DebugClientRegistration(
            clientId: clientId,
            appName: identity.appName,
            bundleId: identity.bundleId,
            platform: platform,
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            capabilities: Self.capabilities(config),
            appVersion: identity.appVersion,
            build: identity.build,
            executable: identity.executable,
            urlSchemes: identity.urlSchemes,
            frameworks: identity.frameworks,
            device: device
        )
    }

    func currentTransport() -> DebugTransporting? {
        lock.lock()
        defer { lock.unlock() }
        return transport
    }

    private func currentConfiguration() -> CaptureConfiguration? {
        lock.lock()
        defer { lock.unlock() }
        return configuration
    }
}

/// Settings pushed from the dashboard.
extension DebugBridgeService {
    /// Uses new settings at once and re-registers so the dashboard sees the new capabilities.
    fileprivate func apply(_ updated: CaptureConfiguration) {
        lock.lock()
        guard configuration != nil else {
            lock.unlock()
            return
        }
        configuration = updated
        needsRegistration = true
        lock.unlock()
        CaptureRuntime.update(configuration: updated)
        Self.applyCrashReplay(updated)
        if updated.debugModules.contains(.ui) {
            FocusDebugMonitor.shared.install()
        }
    }

    fileprivate func takeNeedsRegistration() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let value = needsRegistration
        needsRegistration = false
        return value
    }
}

/// Foreground, background, and network-change handling for the bridge loop.
extension DebugBridgeService {
    fileprivate func handle(_ event: ConnectionLifecycleEvent) {
        switch event {
        case .foreground:
            resume(reason: "foreground")
        case .networkChanged:
            resume(reason: "network")
        case .background:
            Task { [weak self] in
                await self?.announceBackground()
            }
        }
    }

    /// Back from background or on a new network: drop stale sockets and register again at once.
    private func resume(reason: String) {
        lock.lock()
        wakeReason = reason
        let active = transport
        lock.unlock()
        Self.log.info("Resuming: \(reason, privacy: .public)")
        (active as? ConnectionRecovering)?.reconnect(reason: reason)
    }

    /// Tells the dashboard this app is suspended, not gone. iOS and tvOS give a few seconds to send it.
    private func announceBackground() async {
        guard let active = currentTransport() else { return }
        #if os(iOS) || os(tvOS)
        let grace = BackgroundGrace()
        await grace.begin()
        defer { grace.end() }
        #endif
        let event = DebugEvent(
            module: "lifecycle",
            kind: "background",
            payload: ["reason": .string("didEnterBackground")]
        )
        try? await active.send(event: event, clientId: clientId)
    }

    /// Reports the return, with the connection failures seen meanwhile so a stuck reconnect is diagnosable.
    fileprivate func announceResume(reason: String, transport: DebugTransporting) async {
        let failures = (transport as? ConnectionRecovering)?.takeDiagnostics() ?? []
        let event = DebugEvent(
            module: "lifecycle",
            kind: reason == "network" ? "network" : "foreground",
            payload: [
                "reason": .string(reason),
                "recent_failures": .array(failures.map { JSONValue.string($0) })
            ]
        )
        try? await transport.send(event: event, clientId: clientId)
    }

    fileprivate func takeWakeReason() -> String? {
        lock.lock()
        defer { lock.unlock() }
        let reason = wakeReason
        wakeReason = nil
        return reason
    }

    private func hasPendingWake() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return wakeReason != nil
    }

    fileprivate func interruptibleSleep(milliseconds: UInt64) async {
        var waited: UInt64 = 0
        while waited < milliseconds, !Task.isCancelled {
            if hasPendingWake() { return }
            try? await Task.sleep(nanoseconds: 50_000_000)
            waited += 50
        }
    }
}
#endif
