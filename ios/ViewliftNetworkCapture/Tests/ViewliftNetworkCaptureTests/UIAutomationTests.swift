import CoreGraphics
import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class UIAutomationTests: XCTestCase {
    private func element(
        role: String = "button",
        label: String = "Sign In",
        identifier: String = "login.submit",
        value: String = ""
    ) -> AccessibilityElementInfo {
        AccessibilityElementInfo(
            role: role,
            label: label,
            identifier: identifier,
            value: value,
            frame: CGRect(x: 10, y: 20, width: 100, height: 44)
        )
    }

    func testPayloadReadsTolerateWrongTypes() {
        let payload = AutomationPayload([
            "label": .string("  Play  "),
            "index": .string("2"),
            "count": .number(500),
            "submit": .string("true"),
            "x": .string("not a number"),
            "text": .string(" keep spaces ")
        ])
        XCTAssertEqual(payload.string("label"), "Play")
        XCTAssertEqual(payload.int("index", fallback: 0, range: 0...10), 2)
        XCTAssertEqual(payload.int("count", fallback: 1, range: 1...200), 200)
        XCTAssertTrue(payload.bool("submit"))
        XCTAssertNil(payload.number("x"))
        XCTAssertEqual(payload.text("text"), " keep spaces ")
        XCTAssertEqual(payload.string("missing"), "")
    }

    func testQueryMatchesLabelCaseInsensitivelyAndIdentifierExactly() {
        XCTAssertTrue(ElementQuery(label: "sign in").matches(element()))
        XCTAssertFalse(ElementQuery(label: "Sign").matches(element()))
        XCTAssertTrue(ElementQuery(identifier: "login.submit").matches(element()))
        XCTAssertFalse(ElementQuery(identifier: "LOGIN.SUBMIT").matches(element()))
    }

    func testQueryTextSearchesLabelIdentifierAndValue() {
        let field = element(role: "textfield", label: "Email", identifier: "email", value: "user@example.com")
        XCTAssertTrue(ElementQuery(text: "example").matches(field))
        XCTAssertTrue(ElementQuery(text: "EMAIL").matches(field))
        XCTAssertFalse(ElementQuery(text: "password").matches(field))
    }

    func testQueryRoleIgnoresSpacesAndCase() {
        let field = element(role: "securetextfield")
        XCTAssertTrue(ElementQuery(role: "Text Field").matches(field))
        XCTAssertFalse(ElementQuery(role: "button").matches(field))
    }

    func testQueryFromPayloadAndEmptiness() {
        XCTAssertTrue(ElementQuery(payload: AutomationPayload([:])).isEmpty)
        let query = ElementQuery(payload: AutomationPayload(["view_id": .string("42"), "index": .number(1)]))
        XCTAssertFalse(query.isEmpty)
        XCTAssertEqual(query.viewId, "42")
        XCTAssertEqual(query.index, 1)
    }

    func testElementJSONUsesFrameObject() {
        var info = element()
        info.viewId = "7"
        guard case let .object(fields) = info.json(), case let .object(frame) = fields["frame"] else {
            return XCTFail("expected object")
        }
        XCTAssertEqual(frame["width"], .number(100))
        XCTAssertEqual(fields["view_id"], .string("7"))
        XCTAssertNil(fields["focused"])
    }

    func testQueryScopesToWindowAndScene() {
        var info = element()
        info.windowId = "w1"
        info.sceneId = "s1"
        let payload = AutomationPayload(["label": .string("Sign In"), "window_id": .string("w1")])
        XCTAssertTrue(ElementQuery(payload: payload).matches(info))
        let otherWindow = AutomationPayload(["label": .string("Sign In"), "window_id": .string("w2")])
        XCTAssertFalse(ElementQuery(payload: otherWindow).matches(info))
        let otherScene = AutomationPayload(["label": .string("Sign In"), "scene_id": .string("s2")])
        XCTAssertFalse(ElementQuery(payload: otherScene).matches(info))
    }

    func testElementJSONAddsScreenFrameOnlyWhenItDiffers() {
        var info = element()
        info.windowId = "w1"
        info.sceneId = "s1"
        info.screenFrame = info.frame
        guard case let .object(same) = info.json() else { return XCTFail("expected object") }
        XCTAssertNil(same["screen_frame"])
        XCTAssertEqual(same["window_id"], .string("w1"))
        XCTAssertEqual(same["scene_id"], .string("s1"))
        info.screenFrame = info.frame.offsetBy(dx: 300, dy: 0)
        guard case let .object(moved) = info.json(), case let .object(screen) = moved["screen_frame"] else {
            return XCTFail("expected screen frame")
        }
        XCTAssertEqual(screen["x"], .number(310))
    }

    #if canImport(UIKit)
    @MainActor
    func testScreenLookupIsSafeWithoutAWindow() {
        XCTAssertNil(AutomationScreen.window(windowId: "missing", sceneId: ""))
        XCTAssertNil(AutomationScreen.window(windowId: "", sceneId: "missing"))
    }
    #endif

    func testRoleKindWinsOverTraits() {
        XCTAssertEqual(AccessibilityRole.resolve(kind: "textfield", traits: ["button"]), "textfield")
        XCTAssertEqual(AccessibilityRole.resolve(kind: "", traits: ["image", "button"]), "button")
        XCTAssertEqual(AccessibilityRole.resolve(kind: "", traits: ["header", "statictext"]), "heading")
        XCTAssertEqual(AccessibilityRole.resolve(kind: "", traits: ["toggle", "button"]), "switch")
        XCTAssertEqual(AccessibilityRole.resolve(kind: "", traits: []), "element")
    }

    func testSwipeMapsToOppositeScrollDirection() {
        XCTAssertEqual(SwipeDirection.up.scrollDirection, .down)
        XCTAssertEqual(SwipeDirection.left.scrollDirection, .right)
    }

    func testScrollTargetClampsToContentEdges() {
        let metrics = ScrollMetrics(
            offset: .zero,
            contentSize: CGSize(width: 300, height: 1_000),
            viewport: CGSize(width: 300, height: 600)
        )
        XCTAssertEqual(metrics.target(.down, fraction: 0.8), CGPoint(x: 0, y: 400))
        XCTAssertEqual(metrics.target(.up, fraction: 0.8), .zero)
        XCTAssertFalse(metrics.canScroll(.up))
        XCTAssertFalse(metrics.canScroll(.right))
        XCTAssertTrue(metrics.canScroll(.down))
    }

    func testScrollInsetsExtendTheRange() {
        let metrics = ScrollMetrics(
            offset: CGPoint(x: 0, y: -50),
            contentSize: CGSize(width: 300, height: 600),
            viewport: CGSize(width: 300, height: 600),
            insets: ScrollInsets(top: 50, bottom: 30)
        )
        XCTAssertEqual(metrics.minOffset.y, -50)
        XCTAssertEqual(metrics.maxOffset.y, 30)
        XCTAssertEqual(metrics.target(.down, fraction: 1), CGPoint(x: 0, y: 30))
    }

    func testFocusNavigatorPrefersAlignedNeighbour() {
        let current = CGRect(x: 0, y: 0, width: 100, height: 100)
        let candidates = [
            CGRect(x: 120, y: 300, width: 100, height: 100),
            CGRect(x: 140, y: 0, width: 100, height: 100),
            CGRect(x: -150, y: 0, width: 100, height: 100)
        ]
        XCTAssertEqual(FocusNavigator.best(from: current, candidates: candidates, direction: .right), 1)
        XCTAssertEqual(FocusNavigator.best(from: current, candidates: candidates, direction: .left), 2)
        XCTAssertEqual(FocusNavigator.best(from: current, candidates: candidates, direction: .down), 0)
        XCTAssertNil(FocusNavigator.best(from: current, candidates: candidates, direction: .up))
    }

    func testAutomationCommandsAreRead() {
        XCTAssertTrue(DebugCommandExecutor.automationCommands.contains("ui.tap"))
        XCTAssertTrue(DebugCommandExecutor.automationCommands.contains("ui.remote"))
        XCTAssertFalse(DebugCommandExecutor.automationCommands.contains("ui.hierarchy"))
    }
}
