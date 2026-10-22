#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import OSLog

#if canImport(UIKit)
import UIKit
#endif

public final class DebugBridgeService: @unchecked Sendable {
    public static let shared = DebugBridgeService()

    private static let log = Logger(
        subsystem: "com.viewlift.networkcapture",
        category: "debug-bridge"
    )

    private let performance = PerformanceCollector()
    private lazy var executor = DebugCommandExecutor(performance: performance)
    private let lock = NSLock()
    private var task: Task<Void, Never>?
    private var transport: DebugTransporting?
    private var configuration: CaptureConfiguration?
    private let clientId: String

    private init() {
        let key = "com.viewlift.networkcapture.client-id"
        if let existing = UserDefaults.standard.string(forKey: key) {
            clientId = existing
        } else {
            let generated = UUID().uuidString
            UserDefaults.standard.set(generated, forKey: key)
            clientId = generated
        }
    }

    public func start(
        configuration: CaptureConfiguration,
        transport: DebugTransporting
    ) {
        guard !configuration.debugModules.isEmpty else { return }
        stop()
        lock.lock()
        self.transport = transport
        self.configuration = configuration
        lock.unlock()
        performance.start()
        MemoryDebugService.shared.installLifecycleTracking()
        MacroRecorder.shared.install()
        CrashHangRecorder.shared.install()
        task = Task { [weak self] in
            await self?.run()
        }
    }

    public func stop() {
        lock.lock()
        let active = task
        task = nil
        transport = nil
        configuration = nil
        lock.unlock()
        active?.cancel()
        performance.stop()
    }

    private func run() async {
        var registered = false
        var lastPerformance = Date.distantPast
        while !Task.isCancelled {
            guard let transport = currentTransport(), let config = currentConfiguration() else {
                return
            }
            do {
                if !registered {
                    try await transport.register(client: registration(config))
                    registered = true
                    Self.log.info("Remote debug bridge registered")
                }
                if config.debugModules.contains(.performance),
                   Date().timeIntervalSince(lastPerformance)
                    >= config.performanceSampleInterval {
                    try await transport.send(
                        event: DebugEvent(
                            module: "performance",
                            kind: "sample",
                            payload: performance.sample()
                        ),
                        clientId: clientId
                    )
                    lastPerformance = Date()
                }
                let commands = try await transport.pollCommands(clientId: clientId)
                for command in commands where command.expiresAt > Date().timeIntervalSince1970 {
                    await execute(command, transport: transport, config: config)
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            } catch {
                registered = false
                Self.log.error(
                    "Debug bridge connection failed: \(String(describing: error), privacy: .public)"
                )
                try? await Task.sleep(nanoseconds: 2_000_000_000)
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

    private func registration(_ config: CaptureConfiguration) -> DebugClientRegistration {
        let info = Bundle.main.infoDictionary
        let name = (info?["CFBundleDisplayName"] as? String)
            ?? (info?["CFBundleName"] as? String)
            ?? "Unknown"
        #if os(tvOS)
        let platform = "tvOS"
        #elseif os(iOS)
        let platform = "iOS"
        #else
        let platform = "macOS"
        #endif
        return DebugClientRegistration(
            clientId: clientId,
            appName: name,
            bundleId: Bundle.main.bundleIdentifier ?? "",
            platform: platform,
            osVersion: ProcessInfo.processInfo.operatingSystemVersionString,
            capabilities: capabilities(config)
        )
    }

    private func capabilities(_ config: CaptureConfiguration) -> [String: DebugCapability] {
        var out = Dictionary(
            uniqueKeysWithValues: DebugModule.allCases.map {
                ($0.rawValue, DebugCapability.unsupported)
            }
        )
        for module in config.debugModules {
            out[module.rawValue] = .available
        }
        if config.debugModules.contains(.memory) {
            out[DebugModule.memory.rawValue] = .partial
        }
        if config.debugModules.contains(.swiftUI) {
            out[DebugModule.swiftUI.rawValue] = .partial
        }
        if config.debugModules.contains(.macro) {
            out[DebugModule.macro.rawValue] = .partial
        }
        #if os(tvOS)
        out[DebugModule.push.rawValue] = .unsupported
        #endif
        return out
    }

    private func currentTransport() -> DebugTransporting? {
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
#endif
