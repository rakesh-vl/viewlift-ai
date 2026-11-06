import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class CensusProbe: NSObject {}

final class CycleOwner {
    var child: CycleChild?
    var onUpdate: (() -> Void)?
}

final class CycleChild {
    var owner: CycleOwner?
}

final class MemoryLeakTests: XCTestCase {
    func testHeapCensusSeesInstanceGrowth() {
        let census = { () -> Int in
            HeapCensus.snapshot { name, _ in name.hasSuffix("CensusProbe") }
                .first { $0.className.hasSuffix("CensusProbe") }?.count ?? 0
        }
        let first = (0..<7).map { _ in CensusProbe() }
        let before = census()
        let second = (0..<5).map { _ in CensusProbe() }
        let after = census()
        XCTAssertGreaterThanOrEqual(before, first.count)
        XCTAssertEqual(after - before, second.count)
    }

    func testRetentionFindsCycleAndClosureHint() {
        let owner = CycleOwner()
        let child = CycleChild()
        owner.child = child
        child.owner = owner
        owner.onUpdate = {}
        let result = RetentionInspector.inspect(root: owner, others: [])
        guard case let .array(cycles) = result["cycles"] else { return XCTFail("missing cycles") }
        XCTAssertEqual(cycles.first, .array([
            .string("CycleOwner"), .string("child"), .string("CycleChild"), .string("owner"), .string("CycleOwner")
        ]))
        guard case let .array(hints) = result["hints"] else { return XCTFail("missing hints") }
        XCTAssertTrue(hints.contains { $0 == .string(
            "Closure CycleOwner.onUpdate may capture self strongly. Use [weak self] in the closure."
        ) })
        child.owner = nil
    }

    func testRetainedByNamesHoldingProperty() {
        let owner = CycleOwner()
        let child = CycleChild()
        owner.child = child
        let result = RetentionInspector.inspect(root: child, others: [(id: "a1", object: owner)])
        XCTAssertEqual(result["retained_by"], .array([.object([
            "id": .string("a1"), "class": .string("CycleOwner"), "property": .string("child")
        ])]))
    }

    func testTrimClipsKeepsNewestReplay() {
        let frame = JSONValue.object(["base64": .string(String(repeating: "a", count: 100))])
        let replay = JSONValue.object(["frames": .array([frame])])
        var events: [[String: JSONValue]] = [["replay": replay], ["replay": replay]]
        LeakReport.trimClips(&events, budget: 150)
        XCTAssertNotNil(events[0]["replay"])
        XCTAssertNil(events[1]["replay"])
        XCTAssertNotNil(events[1]["replay_note"])
    }

    func testReplayClipRespectsBudgetAndOrder() {
        let recorder = LeakReplayRecorder.shared
        recorder.stop()
        let now = Date().timeIntervalSince1970
        for offset in [3.0, 2.0, 1.0] {
            recorder.append(LeakReplayRecorder.Frame(
                time: now - offset, jpeg: Data(count: 300), width: 10, height: 20, screen: "Home", touches: []
            ))
        }
        let clip = recorder.clip(before: Date(), seconds: 10, byteBudget: 1_100)
        guard case let .array(frames)? = clip?["frames"] else { return XCTFail("missing frames") }
        XCTAssertEqual(frames.count, 2)
        guard case let .object(first) = frames[0], case let .number(ago)? = first["ago_ms"] else {
            return XCTFail("bad frame")
        }
        XCTAssertGreaterThan(ago, 1_500)
        recorder.stop()
    }

    func testLiveDrainReportsLoadedAndReleased() {
        let service = MemoryDebugService.shared
        service.startLiveTracking()
        defer { service.stopLiveTracking() }
        _ = service.drainEvents(now: Date().addingTimeInterval(1_000))
        autoreleasepool {
            let probe = CensusProbe()
            service.track(probe, context: "test")
        }
        let events = service.drainEvents(now: Date().addingTimeInterval(2_000))
        guard let live = events.first(where: { $0.kind == "live" }),
              case let .array(activity)? = live.payload["activity"] else {
            return XCTFail("missing live event")
        }
        let kinds = activity.compactMap { item -> String? in
            guard case let .object(row) = item, row["class"] == .string("CensusProbe"),
                  case let .string(kind)? = row["kind"] else { return nil }
            return kind
        }
        XCTAssertEqual(kinds, ["loaded", "released"])
    }
}
