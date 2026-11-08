import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class ConsoleBurstTests: XCTestCase {
    func testMultilineStringStaysOneMessage() {
        let pendingText = "\"Analytics – Tracking: updateCurrent\"\n"
            + "\"PlayheadTracker: Emitting ping\"\n"
            + "\"\\nEND OF LOG\\n\"\n"
        var pending = pendingText
        let message = ConsoleBurst.takeMessage(from: &pending)
        XCTAssertEqual(pending, "")
        XCTAssertEqual(
            message,
            "\"Analytics – Tracking: updateCurrent\"\n\"PlayheadTracker: Emitting ping\"\n\"\\nEND OF LOG\\n\""
        )
    }

    func testSeparatePrintsStaySeparateWhenEachFlushCompletes() {
        var pending = "one\n"
        XCTAssertEqual(ConsoleBurst.takeMessage(from: &pending), "one")
        pending = "two\n"
        XCTAssertEqual(ConsoleBurst.takeMessage(from: &pending), "two")
    }

    func testSplitReadWaitsUntilTheTrailingBreak() {
        var pending = "alpha\nbeta"
        XCTAssertNil(ConsoleBurst.takeMessage(from: &pending))
        XCTAssertEqual(pending, "alpha\nbeta")
        pending += "\ngamma\n"
        XCTAssertEqual(ConsoleBurst.takeMessage(from: &pending), "alpha\nbeta\ngamma")
        XCTAssertEqual(pending, "")
    }

    func testBlankLinesInsideTheStringStay() {
        var pending = "alpha\n\nbeta\n"
        XCTAssertEqual(ConsoleBurst.takeMessage(from: &pending), "alpha\n\nbeta")
    }

    func testCarriageReturnNewlinesStayInsideTheMessage() {
        var pending = "alpha\r\nbeta\r\n"
        XCTAssertEqual(ConsoleBurst.takeMessage(from: &pending), "alpha\r\nbeta")
    }

    func testWhitespaceOnlyBurstIsDropped() {
        var pending = "\n\n"
        XCTAssertNil(ConsoleBurst.takeMessage(from: &pending))
        XCTAssertEqual(pending, "")
    }
}
