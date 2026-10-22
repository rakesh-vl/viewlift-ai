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
    private lazy var session: URLSession = {
        Self.makeReplaySession()
    }()

    static func makeReplaySession() -> URLSession {
        if let factory = replaySessionFactory {
            return factory()
        }
        let config = URLSessionConfiguration.default
        var classes = config.protocolClasses ?? []
        classes.removeAll { $0 == CaptureURLProtocol.self }
        config.protocolClasses = classes
        return URLSession(configuration: config, delegate: nil, delegateQueue: nil)
    }

    // URLProtocol requires `class` overrides (not `static`).
    override public class func canInit(with request: URLRequest) -> Bool {
        guard CaptureRuntime.isActive else { return false }
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
        guard let mutable = (request as NSURLRequest).mutableCopy() as? NSMutableURLRequest else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        URLProtocol.setProperty(
            CaptureRuntime.recursionValue,
            forKey: CaptureRuntime.recursionKey,
            in: mutable
        )
        var replay = mutable as URLRequest
        CaptureRuntime.markInternal(&replay)

        dataTask = session.dataTask(with: replay) { [weak self] data, response, error in
            guard let self else { return }
            if let response {
                self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            }
            if let data, !data.isEmpty {
                self.client?.urlProtocol(self, didLoad: data)
            }
            self.recordExchange(responseData: data ?? Data(), response: response, error: error)
            if let error {
                self.client?.urlProtocol(self, didFailWithError: error)
            } else {
                self.client?.urlProtocolDidFinishLoading(self)
            }
        }
        dataTask?.resume()
    }

    override public func stopLoading() {
        dataTask?.cancel()
        dataTask = nil
    }

    private func recordExchange(responseData: Data, response: URLResponse?, error: Error?) {
        let config = CaptureRuntime.configuration
        let redactor = CaptureRuntime.redactor
        let headers = request.allHTTPHeaderFields ?? [:]
        let respHeaders = Self.stringHeaders(from: response)
        let reqCap = config?.maxRequestBodyBytes ?? 262_144
        let respCap = config?.maxResponseBodyBytes ?? 262_144
        let reqBody = redactor.redact(jsonData: BodyCap.truncate(request.httpBody ?? Data(), maxBytes: reqCap))
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
