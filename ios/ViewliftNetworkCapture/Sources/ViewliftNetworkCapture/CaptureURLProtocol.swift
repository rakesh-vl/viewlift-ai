import Foundation

/// URLProtocol interceptor for URLSession default/ephemeral HTTP(S).
///
/// Replay uses `URLSessionConfiguration.default` with only `CaptureURLProtocol`
/// stripped so shared cookies, URL cache, and credential storage stay in play and
/// request properties (method, headers, body, timeout) are honored.
///
/// Unavoidable limits: custom `URLSessionDelegate` challenge/redirect handling on the
/// app session is not forwarded; background `URLSession` configurations are not
/// intercepted. See README.
public class CaptureURLProtocol: URLProtocol, @unchecked Sendable {
    /// Test seam: override replay session (must not re-enter CaptureURLProtocol).
    static var replaySessionFactory: (() -> URLSession)?

    private var dataTask: URLSessionDataTask?
    private var startedAt: CFAbsoluteTime = 0
    private var responseAt: CFAbsoluteTime = 0
    private var received = Data()
    private var pendingResponse: URLResponse?
    private var finished = false
    private var stopped = false
    private var capturedRequestBody = Data()
    private let probe = ReplayProbe()
    private lazy var session: URLSession = {
        if Self.replaySessionFactory != nil {
            return Self.makeReplaySession()
        }
        probe.owner = self
        return Self.makeSession(delegate: probe)
    }()

    static func makeReplaySession() -> URLSession {
        if let factory = replaySessionFactory {
            return factory()
        }
        return makeSession(delegate: nil)
    }

    static func makeSession(delegate: URLSessionDelegate?) -> URLSession {
        let config = URLSessionConfiguration.default
        var classes = config.protocolClasses ?? []
        classes.removeAll { $0 == CaptureURLProtocol.self }
        config.protocolClasses = classes
        return URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }

    // URLProtocol requires `class` overrides (not `static`).
    override public class func canInit(with request: URLRequest) -> Bool {
        guard CaptureRuntime.isCapturingNetwork else { return false }
        guard let scheme = request.url?.scheme?.lowercased() else { return false }
        guard scheme == "http" || scheme == "https" else { return false }
        if CaptureRuntime.isInternal(request) { return false }
        if URLProtocol.property(forKey: CaptureRuntime.recursionKey, in: request) != nil {
            return false
        }
        return true
    }

    override public class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override public func startLoading() {
        startedAt = CFAbsoluteTimeGetCurrent()
        responseAt = 0
        received = Data()
        pendingResponse = nil
        finished = false
        stopped = false
        guard var replay = replayRequest() else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        capturedRequestBody = RequestBodyBuffer.apply(&replay)
        switch NetworkShaper.shared.decision() {
        case .pass:
            beginReplay(replay)
        case let .delay(seconds):
            let request = replay
            DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + seconds) { [weak self] in
                guard let self, !self.stopped else { return }
                self.beginReplay(request)
            }
        case let .fail(code):
            finishReplay(error: URLError(code))
        }
    }

    private func beginReplay(_ replay: URLRequest) {
        if session.delegate == nil {
            startCompletionTask(replay)
        } else {
            probe.owner = self
            dataTask = session.dataTask(with: replay)
            dataTask?.resume()
        }
    }

    private func replayRequest() -> URLRequest? {
        guard let mutable = (request as NSURLRequest).mutableCopy() as? NSMutableURLRequest else {
            return nil
        }
        URLProtocol.setProperty(
            CaptureRuntime.recursionValue,
            forKey: CaptureRuntime.recursionKey,
            in: mutable
        )
        var replay = mutable as URLRequest
        CaptureRuntime.markInternal(&replay)
        return replay
    }

    private func startCompletionTask(_ replay: URLRequest) {
        dataTask = session.dataTask(with: replay) { [weak self] data, response, error in
            guard let self else { return }
            self.pendingResponse = response
            self.received = data ?? Data()
            self.finishReplay(error: error)
        }
        dataTask?.resume()
    }

    fileprivate func noteResponse(_ response: URLResponse) {
        responseAt = CFAbsoluteTimeGetCurrent()
        pendingResponse = response
    }

    fileprivate func appendBody(_ data: Data) {
        received.append(data)
    }

    fileprivate func finishReplay(error: Error?) {
        if finished { return }
        finished = true
        let throttle = error == nil ? NetworkShaper.shared.transferDelay(bytes: received.count) : 0
        guard throttle > 0 else {
            deliver(error: error)
            return
        }
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + throttle) { [weak self] in
            guard let self, !self.stopped else { return }
            self.deliver(error: error)
        }
    }

    private func deliver(error: Error?) {
        let response = pendingResponse
        let data = received
        if let response {
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        }
        if !data.isEmpty {
            client?.urlProtocol(self, didLoad: data)
        }
        let ttfb = responseAt > 0 ? (responseAt - startedAt) * 1_000 : nil
        recordExchange(responseData: data, response: response, error: error, ttfbMs: ttfb)
        if let error {
            client?.urlProtocol(self, didFailWithError: error)
        } else {
            client?.urlProtocolDidFinishLoading(self)
        }
    }

    override public func stopLoading() {
        stopped = true
        dataTask?.cancel()
        dataTask = nil
    }

    private func recordExchange(
        responseData: Data,
        response: URLResponse?,
        error: Error?,
        ttfbMs: Double?
    ) {
        let config = CaptureRuntime.configuration
        let redactor = CaptureRuntime.redactor
        let headers = request.allHTTPHeaderFields ?? [:]
        let respHeaders = Self.stringHeaders(from: response)
        let reqType = headers["Content-Type"] ?? headers["content-type"]
        let respType = respHeaders["Content-Type"] ?? respHeaders["content-type"]
        let reqCap = config?.keepsBody(contentType: reqType) == false ? 0 : config?.maxRequestBodyBytes ?? 262_144
        let respCap = config?.keepsBody(contentType: respType) == false ? 0 : config?.maxResponseBodyBytes ?? 262_144
        let reqBody = redactor.redact(jsonData: BodyCap.truncate(capturedRequestBody, maxBytes: reqCap))
        let respBody = redactor.redact(jsonData: BodyCap.truncate(responseData, maxBytes: respCap))
        let url = request.url
        CaptureRuntime.publish(
            CapturedHTTPExchange(
                url: url?.absoluteString ?? "",
                method: request.httpMethod ?? "GET",
                host: url?.host ?? "",
                path: url?.path.isEmpty == false ? (url?.path ?? "/") : "/",
                status: (response as? HTTPURLResponse)?.statusCode,
                durationMs: (CFAbsoluteTimeGetCurrent() - startedAt) * 1000.0,
                protocolName: (url?.scheme?.uppercased() == "HTTPS") ? "HTTPS" : "HTTP",
                requestHeaders: redactor.redact(headers: headers),
                requestBodyBase64: BodyCap.base64Capped(reqBody, maxBytes: reqCap),
                responseHeaders: redactor.redact(headers: respHeaders),
                responseBodyBase64: BodyCap.base64Capped(respBody, maxBytes: respCap),
                error: error.map { $0.localizedDescription },
                requestId: headers["X-Request-Id"] ?? headers["x-request-id"] ?? headers["Request-Id"],
                contentType: respHeaders["Content-Type"] ?? respHeaders["content-type"],
                source: "SDK",
                extraRedactionKeys: config?.extraRedactionKeys ?? []
            )
        )
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        if let url {
            PlaybackWaterfall.shared.record(
                PlaybackTransfer(
                    url: url,
                    status: (response as? HTTPURLResponse)?.statusCode,
                    bytes: responseData.count,
                    durationMs: (CFAbsoluteTimeGetCurrent() - startedAt) * 1_000,
                    ttfbMs: ttfbMs,
                    headers: respHeaders
                )
            )
        }
        #endif
    }

    private static func stringHeaders(from response: URLResponse?) -> [String: String] {
        guard let http = response as? HTTPURLResponse else { return [:] }
        return http.allHeaderFields.reduce(into: [String: String]()) { partial, pair in
            if let key = pair.key as? String {
                partial[key] = String(describing: pair.value)
            }
        }
    }
}

/// Weak owner so the replay session does not retain the protocol.
private final class ReplayProbe: NSObject, URLSessionDataDelegate {
    weak var owner: CaptureURLProtocol?

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        owner?.noteResponse(response)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        owner?.appendBody(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        owner?.finishReplay(error: error)
    }
}
