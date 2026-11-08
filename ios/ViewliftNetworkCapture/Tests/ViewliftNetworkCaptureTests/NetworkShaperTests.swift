import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class NetworkShaperTests: XCTestCase {
    func testOffByDefault() {
        let shaper = NetworkShaper()
        XCTAssertNil(shaper.conditions)
        XCTAssertEqual(shaper.decision(), .pass)
        XCTAssertEqual(shaper.transferDelay(bytes: 1_000_000), 0)
    }

    func testPresetAddsLatencyAndThrottle() throws {
        let shaper = NetworkShaper(random: { 99 })
        let result = try shaper.apply(["profile": .string("3g")])
        XCTAssertEqual(result["profile"], .string("3g"))
        XCTAssertEqual(shaper.decision(), .delay(0.2))
        XCTAssertEqual(shaper.transferDelay(bytes: 97_500), 1, accuracy: 0.001)
    }

    func testOfflineFailsEveryRequest() throws {
        let shaper = NetworkShaper()
        _ = try shaper.apply(["profile": .string("offline")])
        XCTAssertEqual(shaper.decision(), .fail(.notConnectedToInternet))
    }

    func testLossOverrideDropsRequests() throws {
        let shaper = NetworkShaper(random: { 10 })
        _ = try shaper.apply(["profile": .string("wifi"), "loss_percent": .number(50), "latency_ms": .number(0)])
        XCTAssertEqual(shaper.decision(), .fail(.networkConnectionLost))
    }

    func testOffClearsConditions() throws {
        let shaper = NetworkShaper()
        _ = try shaper.apply(["profile": .string("edge")])
        _ = try shaper.apply(["profile": .string("off")])
        XCTAssertNil(shaper.conditions)
    }

    func testUnknownProfileThrows() {
        XCTAssertThrowsError(try NetworkShaper().apply(["profile": .string("5g-ultra")]))
    }
}
