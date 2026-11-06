import Foundation
import OSLog

/// Recovery after the app was suspended or the network changed.
protocol ConnectionRecovering: AnyObject {
    func reconnect(reason: String)
    func takeDiagnostics() -> [String]
}

extension PinnedHTTPSTransport: ConnectionRecovering {
    /// Drop stale sockets and the session, search again, and pair without waiting out a backoff.
    func reconnect(reason: String) {
        Self.log.info("Reconnecting: \(reason, privacy: .public)")
        lock.lock()
        sessionToken = nil
        let stale = session
        let running = configuration != nil
        lock.unlock()
        stale?.getAllTasks { tasks in
            tasks.forEach { $0.cancel() }
        }
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        discovery?.restart()
        #endif
        requestWake()
        if running, dripTask == nil || dripTask?.isCancelled == true {
            startDrainLoop()
        }
    }

    func takeDiagnostics() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return route.takeFailures()
    }
}

extension PinnedHTTPSTransport {
    static func receiverURL(host: String, port: Int) -> URL? {
        var parts = URLComponents()
        parts.scheme = "https"
        parts.host = host
        parts.port = port
        return parts.url
    }

    func remember(_ record: DiscoveredReceiver, fingerprint: String) {
        memory.save(
            RememberedReceiver(
                hosts: record.candidateHosts,
                port: record.port,
                fingerprint: fingerprint,
                debugKey: record.debugKey
            )
        )
    }

    /// Reuse the last dashboard so a relaunch, or a phone away from the Mac's Wi-Fi, can connect
    /// (for example over Tailscale) before Bonjour answers.
    func restoreRememberedReceiver() {
        guard let remembered = memory.load() else { return }
        lock.lock()
        defer { lock.unlock() }
        guard var updated = configuration, updated.debugKey.isEmpty, updated.pairingToken.isEmpty else {
            return
        }
        route.candidates.replace(with: remembered.hosts)
        guard let host = route.candidates.current,
              let url = Self.receiverURL(host: host, port: remembered.port) else {
            return
        }
        updated.receiverURL = url
        updated.pinnedCertificateSHA256 = remembered.fingerprint
        updated.debugKey = remembered.debugKey
        configuration = updated
        let port = remembered.port
        Self.log.info("Using remembered dashboard \(host, privacy: .public):\(port, privacy: .public)")
    }

    func requestWake() {
        lock.lock()
        wakeRequested = true
        lock.unlock()
    }

    private func consumeWake() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let requested = wakeRequested
        wakeRequested = false
        return requested
    }

    /// Sleeps in short slices. Returns true when a wake request cut the wait short.
    func sleepUnlessWoken(seconds: UInt64) async -> Bool {
        for _ in 0..<(seconds * 10) {
            if Task.isCancelled { return false }
            if consumeWake() { return true }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        return consumeWake()
    }

    /// One request. Counts connectivity failures and switches to the next known address after repeats.
    func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        let session = try requireSession()
        do {
            let result = try await session.data(for: request)
            noteSuccess()
            return result
        } catch {
            noteFailure(error, path: request.url?.path ?? "")
            throw error
        }
    }

    private func noteSuccess() {
        lock.lock()
        route.recordSuccess()
        lock.unlock()
    }

    private func noteFailure(_ error: Error, path: String) {
        guard TransportFailure.isConnectivity(error) else { return }
        lock.lock()
        let next = route.recordFailure(TransportFailure.summary(path: path, error: error))
        guard let next, var updated = configuration,
              let url = Self.receiverURL(host: next, port: updated.receiverURL.port ?? 0) else {
            lock.unlock()
            return
        }
        updated.receiverURL = url
        configuration = updated
        sessionToken = nil
        wakeRequested = true
        lock.unlock()
        Self.log.info("Trying next dashboard address \(next, privacy: .public)")
    }
}
