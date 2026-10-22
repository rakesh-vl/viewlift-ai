import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class ViewliftNetworkCaptureTests: XCTestCase {
    override func tearDown() {
        CaptureURLProtocol.replaySessionFactory = nil
        CaptureRuntime.deactivate()
        CaptureService.shared.stop()
        super.tearDown()
    }

    func testRedactionHeadersAndNestedJSON() {
        let redactor = HeaderJSONRedactor(extraKeys: ["custom_secret"])
        let headers = redactor.redact(headers: [
            "Authorization": "Bearer abc",
            "X-Trace": "1",
            "custom_secret": "nope"
        ])
        XCTAssertEqual(headers["Authorization"], "Bearer abc")
        XCTAssertEqual(headers["custom_secret"], "[REDACTED]")
        XCTAssertEqual(headers["X-Trace"], "1")

        let json = Data(#"{"token":"t","nested":{"password":"p","ok":true}}"#.utf8)
        let redacted = redactor.redact(jsonData: json)
        let object = try? JSONSerialization.jsonObject(with: redacted) as? [String: Any]
        XCTAssertEqual(object?["token"] as? String, "t")
        let nested = object?["nested"] as? [String: Any]
        XCTAssertEqual(nested?["password"] as? String, "[REDACTED]")
        XCTAssertEqual(nested?["ok"] as? Bool, true)
    }

    func testBodyCapTruncates() {
        let data = Data(repeating: 0x61, count: 100)
        let capped = BodyCap.truncate(data, maxBytes: 10)
        XCTAssertEqual(capped.count, 10)
        XCTAssertNil(BodyCap.base64Capped(Data(), maxBytes: 10))
        XCTAssertNotNil(BodyCap.base64Capped(data, maxBytes: 10))
    }

    func testQueueDropsOldestAndReportsCount() {
        let queue = CaptureQueue(maxDepth: 2)
        queue.enqueue(sampleExchange(id: "1"))
        queue.enqueue(sampleExchange(id: "2"))
        queue.enqueue(sampleExchange(id: "3"))
        XCTAssertEqual(queue.dropCount, 1)
        XCTAssertEqual(queue.dequeue()?.id, "2")
        XCTAssertEqual(queue.dequeue()?.id, "3")
        XCTAssertNil(queue.dequeue())
    }

    func testRecursionMarkerSkipsCanInit() {
        CaptureRuntime.deactivate()
        var request = URLRequest(url: URL(string: "https://example.com/x")!)
        XCTAssertFalse(CaptureURLProtocol.canInit(with: request))

        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc"
        )
        CaptureRuntime.activate(
            configuration: config,
            redactor: HeaderJSONRedactor(),
            queue: CaptureQueue()
        ) { _ in }
        XCTAssertTrue(CaptureURLProtocol.canInit(with: request))

        CaptureRuntime.markInternal(&request)
        XCTAssertFalse(CaptureURLProtocol.canInit(with: request))

        CaptureRuntime.deactivate()
    }

    func testStartStopTogglesRuntime() async {
        let transport = MockTransport()
        let service = CaptureService(transport: transport)
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc",
            maxQueueDepth: 4
        )
        service.start(configuration: config)
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        XCTAssertTrue(service.isRunning)
        XCTAssertTrue(CaptureRuntime.isActive)
        service.stop()
        XCTAssertFalse(service.isRunning)
        XCTAssertFalse(CaptureRuntime.isActive)
        #else
        XCTAssertFalse(service.isRunning)
        #endif
    }

    func testStartInjectsProtocolIntoNewDefaultAndEphemeralConfigurations() {
        let service = CaptureService(transport: MockTransport())
        let configuration = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc"
        )

        service.start(configuration: configuration)
        #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
        XCTAssertTrue(
            URLSessionConfiguration.default.protocolClasses?.contains {
                $0 == CaptureURLProtocol.self
            } == true
        )
        XCTAssertTrue(
            URLSessionConfiguration.ephemeral.protocolClasses?.contains {
                $0 == CaptureURLProtocol.self
            } == true
        )
        #endif
        service.stop()
    }

    func testReplaySessionStripsCaptureProtocolFromDefaultConfiguration() {
        let session = CaptureURLProtocol.makeReplaySession()
        let classes = session.configuration.protocolClasses ?? []
        XCTAssertFalse(classes.contains { $0 == CaptureURLProtocol.self })
    }

    func testInterceptionDeliversResponseThroughCaptureURLProtocol() async throws {
        StubUpstreamURLProtocol.reset()
        StubUpstreamURLProtocol.statusCode = 201
        StubUpstreamURLProtocol.body = Data(#"{"ok":true}"#.utf8)

        CaptureURLProtocol.replaySessionFactory = {
            let config = URLSessionConfiguration.ephemeral
            config.protocolClasses = [StubUpstreamURLProtocol.self]
            return URLSession(configuration: config)
        }

        let queue = CaptureQueue()
        let expectCapture = expectation(description: "capture published")
        var captured: CapturedHTTPExchange?
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc"
        )
        CaptureRuntime.activate(
            configuration: config,
            redactor: HeaderJSONRedactor(),
            queue: queue
        ) { exchange in
            captured = exchange
            expectCapture.fulfill()
        }

        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.protocolClasses = [CaptureURLProtocol.self]
        let session = URLSession(configuration: sessionConfig)
        var request = URLRequest(url: URL(string: "https://capture.test/v1")!)
        request.httpMethod = "GET"
        request.setValue("req-42", forHTTPHeaderField: "X-Request-Id")
        request.timeoutInterval = 5

        let (data, response) = try await session.data(for: request)
        let http = try XCTUnwrap(response as? HTTPURLResponse)
        XCTAssertEqual(http.statusCode, 201)
        XCTAssertEqual(data, StubUpstreamURLProtocol.body)
        XCTAssertGreaterThanOrEqual(StubUpstreamURLProtocol.hitCount, 1)

        await fulfillment(of: [expectCapture], timeout: 2)
        XCTAssertEqual(captured?.status, 201)
        XCTAssertEqual(captured?.requestId, "req-42")
        XCTAssertEqual(captured?.host, "capture.test")

        CaptureRuntime.deactivate()
        CaptureURLProtocol.replaySessionFactory = nil
    }

    func testInterceptionDeliversErrorThroughCaptureURLProtocol() async {
        StubUpstreamURLProtocol.reset()
        StubUpstreamURLProtocol.shouldFail = true

        CaptureURLProtocol.replaySessionFactory = {
            let config = URLSessionConfiguration.ephemeral
            config.protocolClasses = [StubUpstreamURLProtocol.self]
            return URLSession(configuration: config)
        }

        let queue = CaptureQueue()
        let expectCapture = expectation(description: "error capture")
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc"
        )
        CaptureRuntime.activate(
            configuration: config,
            redactor: HeaderJSONRedactor(),
            queue: queue
        ) { exchange in
            XCTAssertNotNil(exchange.error)
            expectCapture.fulfill()
        }

        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.protocolClasses = [CaptureURLProtocol.self]
        let session = URLSession(configuration: sessionConfig)
        let request = URLRequest(url: URL(string: "https://capture.test/fail")!)

        do {
            _ = try await session.data(for: request)
            XCTFail("Expected transport error from CaptureURLProtocol")
        } catch {
            XCTAssertFalse(String(describing: error).isEmpty)
        }

        await fulfillment(of: [expectCapture], timeout: 2)
        CaptureRuntime.deactivate()
        CaptureURLProtocol.replaySessionFactory = nil
    }

    private func sampleExchange(id: String) -> CapturedHTTPExchange {
        CapturedHTTPExchange(
            id: id,
            url: "https://example.com/",
            method: "GET",
            host: "example.com",
            path: "/",
            status: 200,
            durationMs: 1,
            protocolName: "HTTPS",
            requestHeaders: [:],
            requestBodyBase64: nil,
            responseHeaders: [:],
            responseBodyBase64: nil,
            error: nil,
            requestId: nil,
            contentType: nil
        )
    }

    func testJSONValueRoundTrip() throws {
        let original: [String: JSONValue] = [
            "text": .string("hello"),
            "number": .number(7),
            "flag": .bool(true),
            "items": .array([.null, .string("x")])
        ]
        let data = try JSONEncoder().encode(original)
        XCTAssertEqual(try JSONDecoder().decode([String: JSONValue].self, from: data), original)
    }

    func testStateServiceRejectsEscapingPathsAndWriteQueries() {
        let service = StateDebugService()
        XCTAssertThrowsError(try service.listFiles(relativePath: "/etc"))
        XCTAssertThrowsError(
            try service.sqlite(relativePath: "Library/a.db", query: "DELETE FROM users")
        )
        XCTAssertThrowsError(
            try service.sqlite(relativePath: "Library/a.db", query: "SELECT 1; DROP TABLE x")
        )
    }

    func testFeatureFlagRegistrationAndOverride() throws {
        let key = "test-\(UUID().uuidString)"
        DebugFeatureFlags.shared.register(
            DebugFeatureFlag(key: key, title: "Test", defaultValue: false)
        )
        XCTAssertFalse(DebugFeatureFlags.shared.isEnabled(key))
        try DebugFeatureFlags.shared.set(key, enabled: true)
        XCTAssertTrue(DebugFeatureFlags.shared.isEnabled(key))
        try DebugFeatureFlags.shared.set(key, enabled: nil)
        XCTAssertFalse(DebugFeatureFlags.shared.isEnabled(key))
    }

    func testKeychainResponseDeclaresValuesAreNotExposed() {
        let result = StateDebugService().keychainMetadata()
        XCTAssertEqual(result["values_exposed"], .bool(false))
    }

    func testRuntimeBrowserCanFindOwnClass() {
        let result = RuntimeBrowser.search("ViewliftNetworkCaptureTests")
        guard case let .array(classes) = result["classes"] else {
            return XCTFail("classes missing")
        }
        XCTAssertFalse(classes.isEmpty)
    }
}

/// Stub upstream for CaptureURLProtocol replay sessions in tests.
/// Non-final: URLProtocol requires `class` overrides for canInit/canonicalRequest.
private class StubUpstreamURLProtocol: URLProtocol, @unchecked Sendable {
    static var hitCount = 0
    static var statusCode = 200
    static var body = Data()
    static var shouldFail = false

    static func reset() {
        hitCount = 0
        statusCode = 200
        body = Data()
        shouldFail = false
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        Self.hitCount += 1
        if Self.shouldFail {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: Self.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

private final class MockTransport: CaptureTransporting, @unchecked Sendable {
    func start(configuration: CaptureConfiguration) async throws {}
    func stop() {}
    func send(flow: CapturedHTTPExchange) async throws {}
    func send(log: CapturedLogEvent) async throws {}
}
