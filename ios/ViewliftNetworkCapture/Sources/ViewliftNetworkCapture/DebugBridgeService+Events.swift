#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Lets the crashing thread wait a bounded time for the dashboard to take a crash report.
private final class CrashDelivery: @unchecked Sendable {
    private let done = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var sent = false

    func finish(_ delivered: Bool) {
        lock.lock()
        sent = delivered
        lock.unlock()
        done.signal()
    }

    func wait(seconds: TimeInterval) -> Bool {
        guard done.wait(timeout: .now() + seconds) == .success else { return false }
        lock.lock()
        defer { lock.unlock() }
        return sent
    }
}

/// Events the bridge loop pushes without waiting for a dashboard command.
extension DebugBridgeService {
    /// Context is cheap and always kept while crash capture is on. Frames follow the `replay` module.
    static func applyCrashReplay(_ config: CaptureConfiguration) {
        let directory = FatalCrashCoordinator.shared.replayDirectory
        if config.debugModules.contains(.crash) {
            CrashContextWriter.shared.start(directory: directory)
        } else {
            CrashContextWriter.shared.stop()
        }
        if config.debugModules.contains(.replay) {
            CrashReplayRecorder.shared.start(directory: directory)
        } else {
            CrashReplayRecorder.shared.stop()
        }
    }

    func sendPerformanceSample(
        config: CaptureConfiguration,
        transport: DebugTransporting,
        last: Date
    ) async throws -> Date {
        guard config.debugModules.contains(.performance),
              Date().timeIntervalSince(last) >= config.performanceSampleInterval else { return last }
        let sample = performance.sample()
        CrashContextWriter.shared.notePerformance(sample)
        try await transport.send(
            event: DebugEvent(module: "performance", kind: "sample", payload: sample),
            clientId: clientId
        )
        return Date()
    }

    /// Crashes from the last run and any that could not be sent in time. Removed from disk once the dashboard has them.
    func sendCrashEvents(transport: DebugTransporting) async throws {
        for entry in FatalCrashCoordinator.shared.pendingDelivery() {
            do {
                try await transport.send(
                    event: DebugEvent(id: entry.id, module: "crash", kind: "fatal", payload: entry.event),
                    clientId: clientId
                )
            } catch let error as TransportError where error.isPayloadRejection {
                // The receiver will never accept this record. Keeping it would repeat the upload forever.
            }
            FatalCrashCoordinator.shared.markDelivered(id: entry.id)
        }
    }

    /// Sends one crash from the dying process and waits up to `wait` seconds. Returns true when it was delivered.
    func deliverCrashNow(_ entry: CrashStore.Entry, wait: TimeInterval) -> Bool {
        guard let transport = currentTransport() else { return false }
        let outcome = CrashDelivery()
        let event = DebugEvent(id: entry.id, module: "crash", kind: "fatal", payload: entry.event)
        let clientId = clientId
        Task.detached {
            let sent = (try? await transport.send(event: event, clientId: clientId)) != nil
            outcome.finish(sent)
        }
        return outcome.wait(seconds: wait)
    }

    /// A main-thread stall, pushed as it is recorded so every page can show it.
    func sendHangEvents(transport: DebugTransporting) async throws {
        for event in CrashHangRecorder.shared.drainHangs() {
            try await transport.send(
                event: DebugEvent(
                    id: Self.hangEventId(event),
                    module: "hang",
                    kind: "detected",
                    payload: event
                ),
                clientId: clientId
            )
        }
    }

    private static func hangEventId(_ event: [String: JSONValue]) -> String {
        if case let .string(value) = event["id"], !value.isEmpty {
            return value
        }
        return UUID().uuidString
    }

    /// Leak reports and live memory samples, pushed without waiting for a dashboard command.
    func sendMemoryEvents(config: CaptureConfiguration, transport: DebugTransporting) async throws {
        guard config.debugModules.contains(.memory) else { return }
        for event in MemoryDebugService.shared.drainEvents() {
            try await transport.send(event: event, clientId: clientId)
        }
    }

    func sendPlaybackEvents(
        config: CaptureConfiguration,
        transport: DebugTransporting
    ) async throws {
        guard config.debugModules.contains(.playback) else { return }
        await MainActor.run {
            PlaybackDebugService.shared.trackVisiblePlayer()
        }
        if let digest = PlaybackDebugService.shared.liveDigest() {
            try await transport.send(event: DebugEvent(
                module: "playback", kind: "digest", payload: digest), clientId: clientId)
        }
        for event in PlaybackDebugService.shared.drainEvents() {
            try await transport.send(event: event, clientId: clientId)
        }
    }

    func sendFocusEvents(
        config: CaptureConfiguration,
        transport: DebugTransporting
    ) async throws {
        guard config.debugModules.contains(.ui) else { return }
        for event in FocusDebugMonitor.shared.drainEvents() {
            try await transport.send(event: event, clientId: clientId)
        }
    }

    /// Who is signed in, when the host called `reportCaptureSession` with a new identity.
    func sendSessionEvents(transport: DebugTransporting) async throws {
        guard let pending = CaptureSessionStore.shared.peekPending() else { return }
        try await transport.send(
            event: DebugEvent(module: "session", kind: "update", payload: pending.eventPayload()),
            clientId: clientId
        )
        CaptureSessionStore.shared.ack(pending)
    }

    /// Host-reported OTT state changes (login, plan, language), debounced into one event.
    func sendOTTChangeEvents(config: CaptureConfiguration, transport: DebugTransporting) async throws {
        guard config.debugModules.contains(.ott), let sections = OTTDebugRegistry.drainChanged() else { return }
        try await transport.send(
            event: DebugEvent(
                module: "ott",
                kind: "changed",
                payload: ["sections": .array(sections.map(JSONValue.string))]
            ),
            clientId: clientId
        )
    }

    /// Screen and playback samples while the Live tab is tracking.
    func sendActivityEvents(config: CaptureConfiguration, transport: DebugTransporting) async throws {
        guard config.debugModules.contains(.activity) else { return }
        if ActivityDebugService.shared.isPreviewing,
           let frame = await ActivityDebugService.shared.takeFrameIfDue() {
            try await transport.send(event: frame, clientId: clientId)
        }
        for event in ActivityDebugService.shared.drainEvents() {
            try await transport.send(event: event, clientId: clientId)
        }
    }
}
#endif
