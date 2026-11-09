import CommonCrypto
import Foundation
import OSLog
import Security

/// Package-owned URLSession transport with SHA-256 certificate pin. Never intercepted.
public final class PinnedHTTPSTransport: NSObject, CaptureTransporting,
    URLSessionDelegate, @unchecked Sendable {
    static let log = Logger(subsystem: "com.viewlift.networkcapture", category: "transport")
    private static let maxBackoffSeconds: UInt64 = 30

    // Shared with PinnedTransport+Recovery.swift. Always read and written under `lock`.
    let lock = NSLock()
    var configuration: CaptureConfiguration?
    var sessionToken: String?
    var session: URLSession?
    var dripTask: Task<Void, Never>?
    #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
    var discovery: ReceiverDiscovery?
    #endif
    let memory: ReceiverMemoryStoring
    var route = ReceiverRoute()
    var wakeRequested = false

    private let identity: ClientIdentityProviding
    private var deviceKey = ""

    init(identity: ClientIdentityProviding, memory: ReceiverMemoryStoring) {
        self.identity = identity
        self.memory = memory
        super.init()
    }

    public convenience init(identity: ClientIdentityProviding) {
        self.init(identity: identity, memory: UserDefaultsReceiverMemory())
    }

    public override convenience init() {
        self.init(identity: UserDefaultsClientIdentity.shared)
    }

    static let clientHeader = "X-Viewlift-Client-ID"

    /// Pairing runs inside the drain loop with backoff, so a receiver that is offline at launch,
    /// a pending Local Network prompt, or a dashboard restart (lost session) all recover.
    public func start(configuration: CaptureConfiguration) async throws {
        prepareSession(configuration: configuration)
        let receiver = configuration.receiverURL.absoluteString
        Self.log.info("Capture started. Receiver \(receiver, privacy: .public)")
        restoreRememberedReceiver()
        startDiscovery()
        startDrainLoop()
    }

    private func prepareSession(configuration: CaptureConfiguration) {
        lock.lock()
        self.configuration = configuration
        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.protocolClasses = []
        sessionConfig.connectionProxyDictionary = [:]
        sessionConfig.timeoutIntervalForRequest = 15
        sessionConfig.httpAdditionalHeaders = [
            CaptureRuntime.recursionKey: CaptureRuntime.recursionValue
        ]
        self.session = URLSession(
            configuration: sessionConfig,
            delegate: self,
            delegateQueue: nil
        )
        lock.unlock()
    }

    public func stop() {
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        discovery?.stop()
        discovery = nil
        #endif
        lock.lock()
        dripTask?.cancel()
        dripTask = nil
        session?.invalidateAndCancel()
        session = nil
        sessionToken = nil
        configuration = nil
        route = ReceiverRoute()
        wakeRequested = false
        lock.unlock()
    }

    private func pair(using configuration: CaptureConfiguration) async throws {
        let pairingToken = try await resolvePairingToken(using: configuration)
        let url = configuration.receiverURL.appendingPathComponent("v1/pair")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        CaptureRuntime.markInternal(&request)
        await refreshDeviceKey()
        let payload = pairPayload(pairingToken: pairingToken)
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await perform(request)
        try Self.requireSuccess(response, onFailure: TransportError.pairingFailed)
        let decoded = try JSONDecoder().decode(PairResponse.self, from: data)
        storeSessionToken(decoded.sessionToken)
    }

    private func resolvePairingToken(using configuration: CaptureConfiguration) async throws -> String {
        if !configuration.pairingToken.isEmpty {
            return configuration.pairingToken
        }
        guard !configuration.debugKey.isEmpty else { throw TransportError.missingCredentials }
        let url = configuration.receiverURL.appendingPathComponent("v1/debug/pair")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(configuration.debugKey, forHTTPHeaderField: "X-Viewlift-Debug-Key")
        CaptureRuntime.markInternal(&request)
        let (data, response) = try await perform(request)
        try Self.requireSuccess(response, onFailure: TransportError.pairingFailed)
        return try JSONDecoder().decode(DebugPairResponse.self, from: data).pairingToken
    }

    private static func requireSuccess(
        _ response: URLResponse,
        onFailure makeError: (Int) -> TransportError
    ) throws {
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw makeError(status) }
    }

    private func storeSessionToken(_ token: String?) {
        lock.lock()
        sessionToken = token
        lock.unlock()
    }

    private var isPaired: Bool {
        lock.lock()
        defer { lock.unlock() }
        return sessionToken != nil
    }

    /// Debug key arrives from Bonjour after launch. Keep the pair loop short until then.
    private var waitingForDiscovery: Bool {
        lock.lock()
        defer { lock.unlock() }
        return (configuration?.debugKey ?? "").isEmpty
    }

    private func post<T: Encodable>(
        path: String,
        body: T,
        additionalHeaders: [String: String] = [:]
    ) async throws {
        let encoded = try JSONEncoder().encode(body)
        _ = try await request(
            path: path,
            method: "POST",
            body: encoded,
            additionalHeaders: additionalHeaders
        )
    }

    private func request(
        path: String,
        method: String,
        body: Data?,
        additionalHeaders: [String: String]
    ) async throws -> Data {
        let config = try requireConfiguration()
        let token = try requireToken()
        let url = config.receiverURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        for (name, value) in additionalHeaders {
            request.setValue(value, forHTTPHeaderField: name)
        }
        CaptureRuntime.markInternal(&request)
        request.httpBody = body
        let (data, response) = try await perform(request)
        if (response as? HTTPURLResponse)?.statusCode == 401 {
            // Receiver restarted or the session expired. Drop the token so the loop pairs again.
            storeSessionToken(nil)
            requestWake()
        }
        try Self.requireSuccess(response, onFailure: TransportError.uploadFailed)
        return data
    }

    func startDrainLoop() {
        dripTask?.cancel()
        dripTask = Task { [weak self] in
            var backoff: UInt64 = 1
            while !Task.isCancelled {
                guard let self else { return }
                guard self.isPaired else {
                    if await self.pairOnce() {
                        backoff = 1
                    } else {
                        let seconds = self.waitingForDiscovery ? 1 : backoff
                        // A wake (foreground, new address, network change) cancels the wait and the backoff.
                        if await self.sleepUnlessWoken(seconds: seconds) {
                            backoff = 1
                        } else if !self.waitingForDiscovery {
                            backoff = min(backoff * 2, Self.maxBackoffSeconds)
                        }
                    }
                    continue
                }
                if let item = CaptureRuntime.queue.dequeue() {
                    await self.upload(item)
                } else {
                    try? await Task.sleep(nanoseconds: 200_000_000)
                }
            }
        }
    }

    private func pairOnce() async -> Bool {
        do {
            try await pair(using: try requireConfiguration())
            Self.log.info("Paired with receiver")
            return true
        } catch {
            Self.log.error("Pairing failed: \(String(describing: error), privacy: .public)")
            return false
        }
    }

    private func upload(_ item: CapturedHTTPExchange) async {
        do {
            try await send(flow: item)
        } catch TransportError.uploadFailed(let status) where status == 401 {
            Self.log.info("Session expired (receiver restarted?). Re-pairing.")
            storeSessionToken(nil)
            CaptureRuntime.queue.enqueue(item)
        } catch {
            Self.log.error("Upload failed: \(String(describing: error), privacy: .public)")
        }
    }

    func requireSession() throws -> URLSession {
        lock.lock()
        defer { lock.unlock() }
        guard let session else { throw TransportError.notStarted }
        return session
    }

    private func requireConfiguration() throws -> CaptureConfiguration {
        lock.lock()
        defer { lock.unlock() }
        guard let configuration else { throw TransportError.notStarted }
        return configuration
    }

    private func requireToken() throws -> String {
        lock.lock()
        defer { lock.unlock() }
        guard let sessionToken else { throw TransportError.notPaired }
        return sessionToken
    }

}

extension PinnedHTTPSTransport: DebugTransporting {
    public func register(client: DebugClientRegistration) async throws {
        try await post(path: "v1/debug/register", body: client)
    }

    public func send(event: DebugEvent, clientId: String) async throws {
        try await post(
            path: "v1/debug/events",
            body: event,
            additionalHeaders: [Self.clientHeader: clientId]
        )
    }

    public func pollCommands(clientId: String) async throws -> [DebugCommand] {
        let data = try await request(
            path: "v1/debug/commands",
            method: "GET",
            body: nil,
            additionalHeaders: [Self.clientHeader: clientId]
        )
        return try JSONDecoder().decode(DebugCommandResponse.self, from: data).commands
    }
}

extension PinnedHTTPSTransport {
    fileprivate func startDiscovery() {
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        let found = ReceiverDiscovery { [weak self] record in
            self?.adoptDiscoveredReceiver(record)
        }
        found.start()
        discovery = found
        #endif
    }

    fileprivate func adoptDiscoveredReceiver(_ record: DiscoveredReceiver) {
        let fingerprint = record.fingerprint
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: " ", with: "")
            .lowercased()
        let hosts = ReceiverAddress.mergingVPN(
            into: record.candidateHosts,
            remembered: memory.load()?.hosts ?? []
        )
        lock.lock()
        guard var updated = configuration else {
            lock.unlock()
            return
        }
        guard ReceiverAddress.shouldAdopt(currentHost: nil, discovered: record.host) else {
            lock.unlock()
            return
        }
        route.candidates.replace(with: hosts, preferring: record.host)
        let unchanged = updated.receiverURL.host == record.host
            && updated.receiverURL.port == record.port
            && updated.pinnedCertificateSHA256 == fingerprint
            && updated.debugKey == record.debugKey
        if unchanged {
            lock.unlock()
            remember(record, hosts: hosts, fingerprint: fingerprint)
            return
        }
        guard let url = Self.receiverURL(host: record.host, port: record.port) else {
            lock.unlock()
            return
        }
        updated.receiverURL = url
        updated.pinnedCertificateSHA256 = fingerprint
        updated.debugKey = record.debugKey
        configuration = updated
        sessionToken = nil
        lock.unlock()
        remember(record, hosts: hosts, fingerprint: fingerprint)
        requestWake()
        let host = record.host
        let port = record.port
        Self.log.info("Dashboard found at \(host, privacy: .public):\(port, privacy: .public)")
    }

}

extension PinnedHTTPSTransport {
    public func send(flow: CapturedHTTPExchange) async throws {
        try await post(path: "v1/flows", body: flow, additionalHeaders: clientHeaders)
    }

    public func send(log: CapturedLogEvent) async throws {
        try await post(path: "v1/logs", body: log, additionalHeaders: clientHeaders)
    }

    fileprivate var clientHeaders: [String: String] {
        [Self.clientHeader: identity.clientId]
    }

    func pairPayload(pairingToken: String) -> [String: String] {
        lock.lock()
        let key = deviceKey
        lock.unlock()
        return ["pairing_token": pairingToken, "client_id": identity.clientId, "device_key": key]
    }

    fileprivate func refreshDeviceKey() async {
        let device = await MainActor.run { DeviceIdentity.current() }
        lock.lock()
        deviceKey = device.deviceKey
        lock.unlock()
    }
}
