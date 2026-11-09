@testable import ViewliftNetworkCapture
import XCTest

final class TransportErrorTests: XCTestCase {
    func testRefusedPayloadsAreNotConnectionFailures() {
        for status in [400, 413, 422, 429] {
            XCTAssertTrue(TransportError.uploadFailed(status: status).isPayloadRejection, "status \(status)")
        }
    }

    func testSessionAndServerFailuresStillReconnect() {
        for status in [401, 403, 404, 500, 502, 503] {
            XCTAssertFalse(TransportError.uploadFailed(status: status).isPayloadRejection, "status \(status)")
        }
        XCTAssertFalse(TransportError.notPaired.isPayloadRejection)
        XCTAssertFalse(TransportError.pairingFailed(status: 400).isPayloadRejection)
    }
}
