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
    private var commandTask: Task<Void, Never>?
    private var wakeReason: String?
    private var needsRegistration = false
    private var commandsReady = false
    let clientId: String

    /// Loop pacing in milliseconds. A live preview polls faster than an idle app.
    private static let idlePollMilliseconds: UInt64 = 500
    private static let previewPollMilliseconds: UInt64 = 250
    private static let waitingMilliseconds: UInt64 = 250
    private static let failureBackoffMilliseconds: UInt64 = 2_000

    private static var pollMilliseconds: UInt64 {
        ActivityDebugService.shared.isPreviewing ? previewPollMilliseconds : idlePollMilliseconds
    }

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
        lock.lock()
        commandTask = Task { [weak self] in
            await self?.runCommands()
        }
        lock.unlock()
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
        let commands = commandTask
        let monitor = lifecycleMonitor
        task = nil
        commandTask = nil
        lifecycleMonitor = nil
        wakeReason = nil
        transport = nil
        configuration = nil
        needsRegistration = false
        commandsReady = false
        lock.unlock()
        active?.cancel()
        commands?.cancel()
        monitor?.stop()
        performance.stop()
        CrashReplayRecorder.shared.stop()
        CrashContextWriter.shared.stop()
        ObjectHandleRegistry.shared.removeAll()
    }

    /// Registration and telemetry. Commands run on their own loop so a slow upload never delays one.
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
            if !registered {
                setCommandsReady(false)
            }
            do {
                if !registered {
                    let device = await MainActor.run { DeviceIdentity.current() }
                    // Frameworks loaded since launch get a path in the crash record's image table.
                    VLCCrashSignalsRefreshImagePaths()
                    try await transport.register(client: registration(config, device: device))
                    registered = true
                    setCommandsReady(true)
                    Self.log.info("Remote debug bridge registered")
                    if let reason = resumeReason {
                        resumeReason = nil
                        await announceResume(reason: reason, transport: transport)
                    }
                }
                lastPerformance = try await tolerating("performance", fallback: lastPerformance) {
                    try await sendPerformanceSample(config: config, transport: transport, last: lastPerformance)
                }
                try await tolerating("crash") { try await sendCrashEvents(transport: transport) }
                try await tolerating("hang") { try await sendHangEvents(transport: transport) }
                try await tolerating("replay") {
                    try await CrashReplayUploader.shared.sendPending(transport: transport, clientId: clientId)
                }
                try await tolerating("memory") { try await sendMemoryEvents(config: config, transport: transport) }
                try await tolerating("activity") { try await sendActivityEvents(config: config, transport: transport) }
                try await tolerating("playback") { try await sendPlaybackEvents(config: config, transport: transport) }
                try await tolerating("focus") { try await sendFocusEvents(config: config, transport: transport) }
                try await tolerating("ott") { try await sendOTTChangeEvents(config: config, transport: transport) }
                try await tolerating("session") { try await sendSessionEvents(transport: transport) }
                await interruptibleSleep(milliseconds: Self.pollMilliseconds)
            } catch let error as TransportError where error.isWaitingForSession {
                // Transport is still starting or re-pairing. Check again shortly instead of backing off.
                registered = false
                setCommandsReady(false)
                await interruptibleSleep(milliseconds: Self.waitingMilliseconds)
            } catch {
                registered = false
                setCommandsReady(false)
                Self.log.error(
                    "Debug bridge connection failed: \(String(describing: error), privacy: .public)"
                )
                await interruptibleSleep(milliseconds: Self.failureBackoffMilliseconds)
            }
        }
    }

    /// Polls for dashboard commands and runs each batch off the loop, in order within the batch.
    private func runCommands() async {
        while !Task.isCancelled {
            guard let transport = currentTransport(), let config = currentConfiguration() else {
                return
            }
            guard commandsAreReady() else {
                await pausePolling(milliseconds: Self.waitingMilliseconds)
                continue
            }
            do {
                let commands = try await transport.pollCommands(clientId: clientId)
                let now = Date().timeIntervalSince1970
                let live = commands.filter { $0.expiresAt > now }
                if !live.isEmpty {
                    Task { [weak self] in
                        for command in live {
                            await self?.execute(command, transport: transport, config: config)
                        }
                    }
                }
                await pausePolling(milliseconds: Self.pollMilliseconds)
            } catch let error as TransportError where error.isWaitingForSession {
                await pausePolling(milliseconds: Self.waitingMilliseconds)
            } catch {
                // The receiver forgot this app (restart, expired session). Register again, then resume.
                requestRegistration()
                Self.log.error(
                    "Debug command poll failed: \(String(describing: error), privacy: .public)"
                )
                await pausePolling(milliseconds: Self.failureBackoffMilliseconds)
            }
        }
    }

    /// Runs one optional upload step. A payload the receiver refuses is dropped and logged,
    /// so it can neither reset the connection nor repeat on every pass.
    private func tolerating<Value>(
        _ step: String,
        fallback: Value,
        _ body: () async throws -> Value
    ) async throws -> Value {
        do {
            return try await body()
        } catch let error as TransportError where error.isPayloadRejection {
            Self.log.error(
                "Debug bridge dropped a rejected \(step, privacy: .public) upload: \(String(describing: error), privacy: .public)"
            )
            return fallback
        }
    }

    private func tolerating(_ step: String, _ body: () async throws -> Void) async throws {
        try await tolerating(step, fallback: (), body)
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
            device: device,
            session: CaptureSessionStore.shared.currentIdentity()
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

    fileprivate func setCommandsReady(_ ready: Bool) {
        lock.lock()
        commandsReady = ready
        lock.unlock()
    }

    fileprivate func commandsAreReady() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return commandsReady
    }

    /// Asks the main loop to register again and holds commands until it has.
    fileprivate func requestRegistration() {
        lock.lock()
        needsRegistration = true
        commandsReady = false
        lock.unlock()
    }

    /// Sleeps without ending early for a wake event, which only the main loop consumes.
    fileprivate func pausePolling(milliseconds: UInt64) async {
        try? await Task.sleep(nanoseconds: milliseconds * 1_000_000)
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
