@testable import ViewliftNetworkCapture
import XCTest

final class ConstraintCopyTests: XCTestCase {
    private func facts() -> ConstraintFacts {
        ConstraintFacts(
            attribute: "Width",
            relation: "equal",
            constant: 0,
            otherName: "",
            otherAttribute: "",
            hasOther: false,
            multiplier: 1,
            contentSize: false,
            hugging: 250,
            compression: 750,
            priority: 1000,
            active: true
        )
    }

    func testContentSizeReadsAsASentence() {
        var item = facts()
        item.constant = 212.333
        item.contentSize = true
        item.hugging = 251
        item.compression = 750
        XCTAssertEqual(ConstraintCopy.title(item), "Width")
        XCTAssertEqual(ConstraintCopy.summary(item), "Width is 212.3 pt")
        XCTAssertEqual(
            ConstraintCopy.note(item),
            "Content size. Hugging 251. Compression resistance 750"
        )
    }

    func testPinToParentIncludesTheGap() {
        var item = facts()
        item.attribute = "Leading"
        item.constant = 16
        item.otherName = "the parent"
        item.otherAttribute = "Leading"
        item.hasOther = true
        XCTAssertEqual(ConstraintCopy.summary(item), "Leading is the parent's leading, plus 16 pt")
        XCTAssertEqual(ConstraintCopy.note(item), "")
    }

    func testMinimumAndInactive() {
        var item = facts()
        item.attribute = "Height"
        item.relation = "min"
        item.constant = 44
        item.priority = 750
        item.active = false
        XCTAssertEqual(ConstraintCopy.summary(item), "Height is at least 44 pt")
        XCTAssertTrue(ConstraintCopy.note(item).contains("Priority 750"))
        XCTAssertTrue(ConstraintCopy.note(item).contains("Not active"))
    }

    func testMultiplier() {
        var item = facts()
        item.constant = -8
        item.otherName = "the parent"
        item.otherAttribute = "Width"
        item.hasOther = true
        item.multiplier = 0.5
        XCTAssertEqual(ConstraintCopy.summary(item), "Width is 0.5 × the parent's width, minus 8 pt")
    }
}
