import CommonCrypto
import Foundation
import OSLog
import Security

/// Package-owned URLSession transport with SHA-256 certificate pin. Never intercepted.
public final class PinnedHTTPSTransport: NSObject, CaptureTransporting,
    URLSessionDelegate, @unchecked Sendable {
    private static let log = Logger(subsystem: "com.viewlift.networkcapture", category: "transport")
    private static let maxBackoffSeconds: UInt64 = 30

    private let lock = NSLock()
    private var configuration: CaptureConfiguration?
    private var sessionToken: String?
    private var session: URLSession?
    private var dripTask: Task<Void, Never>?
    #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
    private var discovery: ReceiverDiscovery?
    #endif

    public override init() {
        super.init()
    }

    /// Pairing runs inside the drain loop with backoff, so a receiver that is offline at launch,
    /// a pending Local Network prompt, or a dashboard restart (lost session) all recover.
    public func start(configuration: CaptureConfiguration) async throws {
        prepareSession(configuration: configuration)
        let receiver = configuration.receiverURL.absoluteString
        Self.log.info("Capture started. Receiver \(receiver, privacy: .public)")
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
        lock.unlock()
    }

    public func send(flow: CapturedHTTPExchange) async throws {
        try await post(path: "v1/flows", body: flow)
    }

    public func send(log: CapturedLogEvent) async throws {
        try await post(path: "v1/logs", body: log)
    }

    private func pair(using configuration: CaptureConfiguration) async throws {
        let pairingToken = try await resolvePairingToken(using: configuration)
        let url = configuration.receiverURL.appendingPathComponent("v1/pair")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        CaptureRuntime.markInternal(&request)
        let payload = ["pairing_token": pairingToken]
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let session = try requireSession()
        let (data, response) = try await session.data(for: request)
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
        let session = try requireSession()
        let (data, response) = try await session.data(for: request)
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
        let session = try requireSession()
        let (data, response) = try await session.data(for: request)
        try Self.requireSuccess(response, onFailure: TransportError.uploadFailed)
        return data
    }

    private func startDrainLoop() {
        dripTask?.cancel()
        dripTask = Task { [weak self] in
            var backoff: UInt64 = 1
            while !Task.isCancelled {
                guard let self else { return }
                guard self.isPaired else {
                    if await self.pairOnce() {
                        backoff = 1
                    } else {
                        try? await Task.sleep(nanoseconds: backoff * 1_000_000_000)
                        backoff = min(backoff * 2, Self.maxBackoffSeconds)
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

    private func requireSession() throws -> URLSession {
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

    public func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let expected: String?
        lock.lock()
        expected = configuration?.pinnedCertificateSHA256.lowercased()
        lock.unlock()
        guard let expected else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        if evaluatePin(trust: trust, expectedSHA256Hex: expected) {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            Self.log.error("Receiver certificate does not match pinned SHA-256. Update the fingerprint.")
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }

    func evaluatePin(trust: SecTrust, expectedSHA256Hex: String) -> Bool {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            return false
        }
        let data = SecCertificateCopyData(leaf) as Data
        let normalized = expectedSHA256Hex
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: " ", with: "")
            .lowercased()
        // Exact leaf pin authenticates the dashboard's self-signed certificate.
        // Requiring system trust here would reject that certificate before pin comparison.
        return sha256Hex(data) == normalized
    }

    private func sha256Hex(_ data: Data) -> String {
        var hash = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
        data.withUnsafeBytes { buffer in
            _ = CC_SHA256(buffer.baseAddress, CC_LONG(buffer.count), &hash)
        }
        return hash.map { String(format: "%02x", $0) }.joined()
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
            additionalHeaders: ["X-Viewlift-Client-ID": clientId]
        )
    }

    public func pollCommands(clientId: String) async throws -> [DebugCommand] {
        let data = try await request(
            path: "v1/debug/commands",
            method: "GET",
            body: nil,
            additionalHeaders: ["X-Viewlift-Client-ID": clientId]
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
        lock.lock()
        guard var updated = configuration else {
            lock.unlock()
            return
        }
        guard ReceiverAddress.shouldAdopt(currentHost: nil, discovered: record.host) else {
            lock.unlock()
            return
        }
        let unchanged = updated.receiverURL.host == record.host
            && updated.receiverURL.port == record.port
            && updated.pinnedCertificateSHA256 == fingerprint
            && updated.debugKey == record.debugKey
        if unchanged {
            lock.unlock()
            return
        }
        var parts = URLComponents()
        parts.scheme = "https"
        parts.host = record.host
        parts.port = record.port
        guard let url = parts.url else {
            lock.unlock()
            return
        }
        updated.receiverURL = url
        updated.pinnedCertificateSHA256 = fingerprint
        updated.debugKey = record.debugKey
        configuration = updated
        sessionToken = nil
        lock.unlock()
        let host = record.host
        let port = record.port
        Self.log.info("Dashboard found at \(host, privacy: .public):\(port, privacy: .public)")
    }
}

private struct PairResponse: Decodable {
    let sessionToken: String
    enum CodingKeys: String, CodingKey {
        case sessionToken = "session_token"
    }
}

private struct DebugPairResponse: Decodable {
    let pairingToken: String
    enum CodingKeys: String, CodingKey {
        case pairingToken = "pairing_token"
    }
}

enum TransportError: Error {
    case notStarted
    case notPaired
    case missingCredentials
    case pairingFailed(status: Int)
    case uploadFailed(status: Int)
}
