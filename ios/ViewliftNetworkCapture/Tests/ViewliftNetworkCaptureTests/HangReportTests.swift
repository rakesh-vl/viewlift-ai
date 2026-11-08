import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class HangReportTests: XCTestCase {
    func testParseFramesReadsClassFileAndLiveSymbolLine() {
        let frames = CrashHangRecorder.parseFrames([
            "2   OneApp  0x102ab  PlayerViewController.render() + 48 (PlayerViewController.swift:88)",
            "3   OneApp  0x1  closure #1 in PlayerViewController.viewDidLoad()"
        ])
        XCTAssertEqual(frames.count, 2)
        XCTAssertEqual(frames[0]["class"], .string("PlayerViewController"))
        XCTAssertEqual(frames[0]["file"], .string("PlayerViewController.swift"))
        XCTAssertEqual(frames[0]["line"], .number(88))
        XCTAssertEqual(frames[1]["class"], .string("PlayerViewController"))
        XCTAssertEqual(frames[1]["symbol"], .string("closure #1 in PlayerViewController.viewDidLoad()"))
    }

    func testTypeNameSkipsClosurePrefix() {
        let name = CrashHangRecorder.typeName(in: "closure #1 in HomeViewController.scrollViewDidScroll(_:)")
        XCTAssertEqual(name, "HomeViewController")
    }

    func testTouchKindSeparatesTapAndScroll() {
        XCTAssertEqual(HangTouchKind.name(distance: 4), "tap")
        XCTAssertEqual(HangTouchKind.name(distance: 20), "scroll")
        XCTAssertEqual(HangTouchKind.direction(dx: 2, dy: -30), "up")
        XCTAssertEqual(HangTouchKind.direction(dx: 40, dy: 3), "right")
        XCTAssertEqual(HangTouchKind.direction(dx: 2, dy: 1), "")
    }

    func testSuspectPrefersAppImage() {
        let frames: [[String: JSONValue]] = [
            ["image": .string("UIKitCore"), "symbol": .string("UIApplication.main()")],
            [
                "image": .string("OneApp"),
                "class": .string("PlayerViewController"),
                "symbol": .string("PlayerViewController.render()"),
                "file": .string("PlayerViewController.swift"),
                "line": .number(88)
            ]
        ]
        let focus = HangFrameFocus.suspect(frames: frames, executable: "OneApp")
        XCTAssertEqual(focus?["class"], .string("PlayerViewController"))
    }

    func testOldScreenshotsDropFirst() {
        var events: [[String: JSONValue]] = [
            ["screenshot": .object(["base64": .string(String(repeating: "a", count: 100))])],
            ["screenshot": .object(["base64": .string(String(repeating: "b", count: 100))])]
        ]
        HangReport.dropOldScreenshots(&events, budget: 100)
        XCTAssertNil(events[0]["screenshot"])
        XCTAssertNotNil(events[0]["screenshot_note"])
        XCTAssertNotNil(events[1]["screenshot"])
    }

    func testRecentActionsReportAgeAndDirection() {
        let trail = HangInteractionTrail.shared
        let id = ObjectIdentifier(NSObject())
        trail.noteBegan(
            id: id,
            point: (0, 0),
            viewClass: "PlayButton",
            label: "Play",
            screen: "HomeViewController"
        )
        trail.noteEnded(id: id, point: (0, 40))
        let rows = trail.recent(before: Date().addingTimeInterval(1))
        guard case let .object(row) = rows.last else {
            return XCTFail("missing action")
        }
        XCTAssertEqual(row["kind"], .string("scroll"))
        XCTAssertEqual(row["direction"], .string("down"))
        XCTAssertEqual(row["target_class"], .string("PlayButton"))
        XCTAssertEqual(row["screen_class"], .string("HomeViewController"))
        XCTAssertEqual(row["label"], .string("Play"))
    }

    func testDrainHangsReturnsEachStallOnce() {
        let recorder = CrashHangRecorder.shared
        _ = recorder.drainHangs()
        recorder.storeHang(["id": .string("hang-toast-1"), "duration_ms": .number(800)])
        let first = recorder.drainHangs()
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first[0]["id"], .string("hang-toast-1"))
        XCTAssertTrue(recorder.drainHangs().isEmpty)
    }
}
