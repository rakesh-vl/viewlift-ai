import CoreGraphics
@testable import ViewliftNetworkCapture
import XCTest

final class SafeNumericTests: XCTestCase {
    func testSafeIntNeverTraps() {
        XCTAssertEqual(Int(safe: Double.nan), 0)
        XCTAssertEqual(Int(safe: Double.nan, fallback: 7), 7)
        XCTAssertEqual(Int(safe: Double.infinity), Int.safeLimit)
        XCTAssertEqual(Int(safe: -Double.infinity), -Int.safeLimit)
        XCTAssertEqual(Int(safe: 1e30), Int.safeLimit)
        XCTAssertEqual(Int(safe: -1e30), -Int.safeLimit)
        XCTAssertEqual(Int(safe: 12.9), 12)
        XCTAssertEqual(Int(safe: CGFloat.greatestFiniteMagnitude), Int.safeLimit)
        XCTAssertEqual(Int(safe: CGFloat.infinity), Int.safeLimit)
    }

    func testSafeLimitProductFitsInInt() {
        let product = Int.safeLimit.multipliedReportingOverflow(by: Int.safeLimit)
        XCTAssertFalse(product.overflow)
    }

    func testNumberTextHandlesHugeAndNonFiniteValues() {
        XCTAssertEqual(SafeNumber.text(12), "12")
        XCTAssertEqual(SafeNumber.text(12.34), "12.3")
        XCTAssertEqual(SafeNumber.text(.infinity), "inf")
        XCTAssertEqual(SafeNumber.text(-.infinity), "-inf")
        XCTAssertEqual(SafeNumber.text(.nan), "nan")
        XCTAssertFalse(SafeNumber.text(1e30).isEmpty)
        XCTAssertFalse(SafeNumber.text(Double.greatestFiniteMagnitude).isEmpty)
    }

    func testPayloadIntClampsHugeNumbers() {
        let payload = AutomationPayload(["huge": .number(1e30), "text": .string("1e30")])
        XCTAssertEqual(payload.int("huge", fallback: 1, range: 1...200), 200)
        XCTAssertEqual(payload.int("text", fallback: 1, range: 1...200), 200)
        XCTAssertEqual(payload.string("huge"), "1e+30")
    }
}
