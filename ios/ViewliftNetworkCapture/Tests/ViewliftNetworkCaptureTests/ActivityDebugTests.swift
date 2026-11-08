import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class HostActivity: ActivityDebugProviding {
    func activityDebugSnapshot() -> [String: JSONValue] {
        [
            "title": .string("Game 7"),
            "content_id": .string("item-1"),
            "stream_url": .string("https://cdn.example/live.m3u8")
        ]
    }
}

final class ActivityDebugTests: XCTestCase {
    override func tearDown() {
        ActivityDebugRegistry.clear()
        super.tearDown()
    }

    func testPreviewScaleCapsTheLongEdge() {
        let scale = PreviewScale.pixels(width: 820, height: 1180, maxPixel: 640)
        XCTAssertEqual(scale, 640 / 1180, accuracy: 0.0001)
    }

    func testPreviewScaleLeavesAShortWindowAlone() {
        XCTAssertEqual(PreviewScale.pixels(width: 320, height: 480, maxPixel: 640), 1, accuracy: 0.0001)
    }

    func testPreviewFlagFollowsLiveState() async {
        let service = ActivityDebugService.shared
        _ = await service.liveState(enabled: true, preview: true)
        XCTAssertTrue(service.isPreviewing)
        _ = await service.liveState(enabled: true, preview: false)
        XCTAssertFalse(service.isPreviewing)
        _ = await service.liveState(enabled: false)
        XCTAssertFalse(service.isPreviewing)
    }

    func testPlayingProbeKeepsPositionAndRate() {
        let sample = ActivityPlayerProbe.describe(
            control: .playing,
            durationIndefinite: false,
            positionSeconds: 12.5,
            rate: 1
        )
        XCTAssertEqual(sample.state, "playing")
        XCTAssertFalse(sample.isLive)
        XCTAssertEqual(sample.positionSeconds, 12.5)
        XCTAssertEqual(sample.rate, 1)
    }

    func testPausedProbeIsNotLive() {
        let sample = ActivityPlayerProbe.describe(
            control: .paused,
            durationIndefinite: false,
            positionSeconds: 4,
            rate: 0
        )
        XCTAssertEqual(sample, ActivityPlaybackSample(state: "paused", isLive: false, positionSeconds: 4, rate: 0))
    }

    func testIndefiniteDurationIsLiveAndDropsNonFinitePosition() {
        let sample = ActivityPlayerProbe.describe(
            control: .playing,
            durationIndefinite: true,
            positionSeconds: .infinity,
            rate: 1
        )
        XCTAssertEqual(sample.state, "playing")
        XCTAssertTrue(sample.isLive)
        XCTAssertNil(sample.positionSeconds)
    }

    func testHostTitleOverridesProbeAndDropsStreamURL() {
        let payload = ActivityPayload.make(ActivitySampleContext(
            screenClass: "PlayerViewController",
            screenTitle: "Watch",
            lifecycle: "foreground",
            playback: ActivityPlaybackSample(state: "playing", isLive: true, positionSeconds: 8, rate: 1),
            meta: ActivitySampleMeta(
                host: HostActivity().activityDebugSnapshot(),
                capturedAt: 20,
                tracking: true
            )
        ))
        guard case let .object(playback) = payload["playback"] else {
            return XCTFail("missing playback")
        }
        XCTAssertEqual(playback["title"], .string("Game 7"))
        XCTAssertEqual(playback["content_id"], .string("item-1"))
        XCTAssertEqual(playback["state"], .string("playing"))
        XCTAssertEqual(playback["is_live"], .bool(true))
        XCTAssertNil(playback["stream_url"])
        XCTAssertEqual(payload["screen_class"], .string("PlayerViewController"))
        XCTAssertEqual(payload["lifecycle"], .string("foreground"))
        XCTAssertEqual(payload["tracking"], .bool(true))
    }

    func testRegistryUsesStrongHostAndClears() {
        let host = HostActivity()
        ActivityDebugRegistry.register(host)
        XCTAssertEqual(ActivityDebugRegistry.snapshot()["title"], .string("Game 7"))
        ActivityDebugRegistry.clear()
        XCTAssertTrue(ActivityDebugRegistry.snapshot().isEmpty)
    }

    func testRegistryDropsProviderWithoutStrongReference() {
        var host: HostActivity? = HostActivity()
        if let host {
            ActivityDebugRegistry.register(host)
        }
        host = nil
        XCTAssertTrue(ActivityDebugRegistry.snapshot().isEmpty)
    }
}
