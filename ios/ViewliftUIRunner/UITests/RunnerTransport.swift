import CommonCrypto
import Foundation
import Security

enum RunnerTransportError: Error, CustomStringConvertible {
    case status(Int, String)
    case badResponse
    case timedOut

    var description: String {
        switch self {
        case let .status(code, detail): return "HTTP \(code): \(detail)"
        case .badResponse: return "The receiver sent a response that is not a JSON object."
        case .timedOut: return "The receiver did not answer in time."
        }
    }
}

/// Talks to the console receiver the same way the SDK does: debug key, one-use pairing token,
/// session bearer, then the debug command queue. Trust is the receiver's exact leaf certificate.
final class RunnerTransport: NSObject, URLSessionDelegate {
    private let config: RunnerConfig
    private let delegateQueue = OperationQueue()
    private var sessionToken: String?
    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        return URLSession(configuration: configuration, delegate: self, delegateQueue: delegateQueue)
    }()

    init(config: RunnerConfig) {
        self.config = config
        super.init()
    }

    func connect(registration: [String: Any]) throws {
        let pairing = try call("POST", "/v1/debug/pair", headers: ["X-Viewlift-Debug-Key": config.debugKey])
        guard let token = pairing["pairing_token"] as? String else { throw RunnerTransportError.badResponse }
        let session = try call(
            "POST",
            "/v1/pair",
            body: ["pairing_token": token, "client_id": config.clientID, "device_key": config.deviceKey]
        )
        guard let bearer = session["session_token"] as? String else { throw RunnerTransportError.badResponse }
        sessionToken = bearer
        _ = try call("POST", "/v1/debug/register", body: registration, authorized: true)
    }

    func pollCommands() throws -> [[String: Any]] {
        let response = try call("GET", "/v1/debug/commands", authorized: true)
        return response["commands"] as? [[String: Any]] ?? []
    }

    func send(event: [String: Any]) throws {
        _ = try call("POST", "/v1/debug/events", body: event, authorized: true)
    }

    private func call(
        _ method: String,
        _ path: String,
        body: [String: Any]? = nil,
        headers: [String: String] = [:],
        authorized: Bool = false
    ) throws -> [String: Any] {
        var request = URLRequest(url: config.receiverURL.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(config.clientID, forHTTPHeaderField: "X-Viewlift-Client-ID")
        headers.forEach { request.setValue($0.value, forHTTPHeaderField: $0.key) }
        if authorized, let sessionToken {
            request.setValue("Bearer \(sessionToken)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return try perform(request)
    }

    private func perform(_ request: URLRequest) throws -> [String: Any] {
        let done = DispatchSemaphore(value: 0)
        var outcome: Result<(Data, Int), Error> = .failure(RunnerTransportError.timedOut)
        session.dataTask(with: request) { data, response, error in
            if let error {
                outcome = .failure(error)
            } else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                outcome = .success((data ?? Data(), code))
            }
            done.signal()
        }.resume()
        guard done.wait(timeout: .now() + 30) == .success else { throw RunnerTransportError.timedOut }
        let (data, code) = try outcome.get()
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        guard (200..<300).contains(code) else {
            throw RunnerTransportError.status(code, object?["detail"] as? String ?? "")
        }
        guard let object else { throw RunnerTransportError.badResponse }
        return object
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              matchesPin(trust) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    private func matchesPin(_ trust: SecTrust) -> Bool {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return false
        }
        let der = SecCertificateCopyData(leaf) as Data
        var hash = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
        der.withUnsafeBytes { buffer in
            _ = CC_SHA256(buffer.baseAddress, CC_LONG(buffer.count), &hash)
        }
        let expected = config.fingerprint
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: " ", with: "")
            .lowercased()
        return hash.map { String(format: "%02x", $0) }.joined() == expected
    }
}
