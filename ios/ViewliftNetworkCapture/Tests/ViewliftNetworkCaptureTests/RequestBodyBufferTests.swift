import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class RequestBodyBufferTests: XCTestCase {
    override func tearDown() {
        CaptureURLProtocol.replaySessionFactory = nil
        CaptureRuntime.deactivate()
        super.tearDown()
    }

    func testReadsStreamAndRewindsReplay() {
        var request = URLRequest(url: URL(string: "https://golf.example/graphql")!)
        let payload = Data(#"{"query":"query Home { id }","variables":{}}"#.utf8)
        request.httpBody = nil
        request.httpBodyStream = InputStream(data: payload)
        let body = RequestBodyBuffer.apply(&request)
        XCTAssertEqual(body, payload)
        XCTAssertEqual(request.httpBody, payload)
        XCTAssertNil(request.httpBodyStream)
    }

    func testLeavesOversizedStream() {
        var request = URLRequest(url: URL(string: "https://golf.example/upload")!)
        request.setValue("\(RequestBodyBuffer.replayCap + 1)", forHTTPHeaderField: "Content-Length")
        let stream = InputStream(data: Data([1, 2, 3]))
        request.httpBodyStream = stream
        let body = RequestBodyBuffer.apply(&request)
        XCTAssertTrue(body.isEmpty)
        XCTAssertTrue(request.httpBodyStream === stream)
    }

    func testInterceptionCapturesGraphQLStreamBody() async throws {
        StubUpstreamURLProtocol.reset()
        StubUpstreamURLProtocol.statusCode = 200
        StubUpstreamURLProtocol.body = Data(#"{"data":{"id":"1"}}"#.utf8)

        CaptureURLProtocol.replaySessionFactory = {
            let config = URLSessionConfiguration.ephemeral
            config.protocolClasses = [StubUpstreamURLProtocol.self]
            return URLSession(configuration: config)
        }

        let expectCapture = expectation(description: "graphql body captured")
        var captured: CapturedHTTPExchange?
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:8742")!,
            pairingToken: "pair",
            pinnedCertificateSHA256: "abc"
        )
        CaptureRuntime.activate(
            configuration: config,
            redactor: HeaderJSONRedactor(),
            queue: CaptureQueue()
        ) { exchange in
            captured = exchange
            expectCapture.fulfill()
        }

        let payload = Data(#"{"query":"query Home { id }","variables":{}}"#.utf8)
        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.protocolClasses = [CaptureURLProtocol.self]
        let session = URLSession(configuration: sessionConfig)
        var request = URLRequest(url: URL(string: "https://capture.test/graphql")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBodyStream = InputStream(data: payload)
        request.timeoutInterval = 5

        let (data, response) = try await session.data(for: request)
        let http = try XCTUnwrap(response as? HTTPURLResponse)
        XCTAssertEqual(http.statusCode, 200)
        XCTAssertEqual(data, StubUpstreamURLProtocol.body)
        XCTAssertEqual(StubUpstreamURLProtocol.lastBody, payload)

        await fulfillment(of: [expectCapture], timeout: 2)
        let encoded = try XCTUnwrap(captured?.requestBodyBase64)
        let capturedBody = try XCTUnwrap(Data(base64Encoded: encoded))
        let capturedJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: capturedBody) as? NSDictionary)
        let payloadJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: payload) as? NSDictionary)
        XCTAssertEqual(capturedJSON, payloadJSON)
    }
}
