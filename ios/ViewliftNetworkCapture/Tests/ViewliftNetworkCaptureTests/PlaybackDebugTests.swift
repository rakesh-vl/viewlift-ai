import AVFoundation
import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class PlaybackDebugTests: XCTestCase {
    override func tearDown() {
        PlaybackDebugService.shared.resetForTests()
        super.tearDown()
    }

    func testResourceKindIgnoresQueryAndSkipsAPICalls() {
        let playlist = URL(string: "https://cdn.example/master.m3u8?token=secret")!
        XCTAssertEqual(PlaybackResource.kind(for: playlist), .playlist)
        XCTAssertEqual(PlaybackResource.path(for: playlist), "/master.m3u8")
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/manifest.mpd")!), .manifest)
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/init.mp4")!), .initialization)
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/seg-1.m4s")!), .segment)
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/seg.ts")!), .segment)
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/a.cmfv")!), .segment)
        XCTAssertEqual(PlaybackResource.kind(for: URL(string: "https://cdn.example/a.cmfa")!), .segment)
        XCTAssertNil(PlaybackResource.kind(for: URL(string: "https://api.example/entitlement")!))
    }

    func testWaterfallDropsQueryTokenAndAuthorization() throws {
        let url = URL(string: "https://cdn.example/master.m3u8?token=secret")!
        PlaybackWaterfall.shared.record(
            PlaybackTransfer(
                url: url,
                status: 200,
                bytes: 40,
                durationMs: 12,
                ttfbMs: 4,
                headers: ["Cache-Control": "max-age=1", "Authorization": "Bearer secret"]
            )
        )
        PlaybackWaterfall.shared.record(
            PlaybackTransfer(
                url: URL(string: "https://api.example/entitlement")!,
                status: 200,
                bytes: 1,
                durationMs: 1,
                ttfbMs: nil,
                headers: [:]
            )
        )
        let snap = PlaybackWaterfall.shared.snapshot()
        guard case let .array(entries) = snap["entries"] else {
            return XCTFail("entries missing")
        }
        XCTAssertEqual(entries.count, 1)
        let encoded = String(bytes: try JSONEncoder().encode(snap), encoding: .utf8) ?? ""
        XCTAssertFalse(encoded.contains("secret"))
        XCTAssertFalse(encoded.contains("token"))
        XCTAssertTrue(encoded.contains("/master.m3u8"))
        XCTAssertTrue(encoded.contains("max-age=1"))
    }

    func testDRMNoteKeepsTimingAndDropsKeyMaterial() {
        let service = PlaybackDebugService.shared
        service.updateIdentity(contentId: "item-9", playbackId: "play-2")
        service.noteDRM(
            PlaybackDRMNote(
                phase: .failed,
                persistent: true,
                errorDomain: "AVFoundation",
                errorCode: -11_800,
                durationMs: 15
            )
        )
        let snap = service.drmSnapshot()
        XCTAssertEqual(snap["content_id"], .string("item-9"))
        XCTAssertEqual(snap["secrets_exposed"], .bool(false))
        guard case let .array(events) = snap["events"],
              let first = events.first,
              case let .object(row) = first else {
            return XCTFail("drm event missing")
        }
        XCTAssertEqual(row["phase"], .string("failed"))
        XCTAssertEqual(row["persistent"], .bool(true))
        XCTAssertEqual(row["error_domain"], .string("AVFoundation"))
        XCTAssertEqual(row["error_code"], .number(-11_800))
        XCTAssertEqual(row["duration_ms"], .number(15))
        XCTAssertNil(row["spc"])
        XCTAssertNil(row["ckc"])
    }

    func testAdBreakRingStaysBoundedAndKeepsNewest() {
        let service = PlaybackDebugService.shared
        let limit = PlaybackDebugService.maxEvents
        for index in 0..<(limit + 10) {
            service.noteAdBreak(started: true, identifier: "ad-\(index)")
        }
        let snap = service.snapshot()
        XCTAssertEqual(snap["count"], .number(Double(limit)))
        guard case let .array(events) = snap["events"],
              let newest = events.first,
              case let .object(row) = newest else {
            return XCTFail("timeline missing")
        }
        XCTAssertEqual(row["ad_id"], .string("ad-\(limit + 9)"))
        XCTAssertEqual(row["kind"], .string("ad_break_start"))
    }

    func testDRMProviderDrainLandsOnTheTimelineOnce() {
        let service = PlaybackDebugService.shared
        let provider = SampleDRMProvider()
        provider.notes = [PlaybackDRMNote(phase: .requestStarted)]
        service.registerDRMProvider(provider)
        let first = service.drmSnapshot()
        let second = service.drmSnapshot()
        guard case let .array(events) = first["events"] else {
            return XCTFail("missing events")
        }
        XCTAssertEqual(events.count, 1)
        guard case let .array(again) = second["events"] else {
            return XCTFail("missing second events")
        }
        XCTAssertEqual(again.count, 1)
        XCTAssertTrue(provider.notes.isEmpty)
    }

    func testAttachLabelsThePlayerWithoutReadingAURL() {
        let service = PlaybackDebugService.shared
        let player = AVPlayer()
        let finished = expectation(description: "attached")
        DispatchQueue.main.async {
            service.attach(player: player, contentId: "cid", playbackId: "pid")
            let snap = service.snapshot()
            XCTAssertEqual(snap["attached"], .bool(true))
            XCTAssertEqual(snap["content_id"], .string("cid"))
            XCTAssertEqual(snap["playback_id"], .string("pid"))
            XCTAssertEqual(snap["secrets_exposed"], .bool(false))
            service.detach()
            XCTAssertEqual(service.snapshot()["attached"], .bool(false))
            finished.fulfill()
        }
        wait(for: [finished], timeout: 2)
    }

    func testLiveDigestOmitsTimelineAndThrottles() {
        let service = PlaybackDebugService.shared
        let player = AVPlayer()
        let finished = expectation(description: "digest")
        DispatchQueue.main.async {
            service.attach(player: player, contentId: "cid", playbackId: "pid")
            service.update { digest in
                digest.status = "playing"
                digest.lag = 50.74045667799999
                digest.observed = 6_207_740
            }
            let now = Date(timeIntervalSince1970: 1_000)
            let first = service.liveDigest(now: now)
            XCTAssertEqual(first?["status"], .string("playing"))
            XCTAssertEqual(first?["live_edge_lag_sec"], .number(50.74045667799999))
            XCTAssertEqual(first?["observed_bps"], .number(6_207_740))
            XCTAssertNil(first?["events"])
            XCTAssertNil(service.liveDigest(now: now.addingTimeInterval(0.4)))
            XCTAssertNotNil(service.liveDigest(now: now.addingTimeInterval(1)))
            service.detach()
            XCTAssertNil(service.liveDigest(now: now.addingTimeInterval(3)))
            finished.fulfill()
        }
        wait(for: [finished], timeout: 2)
    }

    func testNavigationURLStripsSecretsAndRejectsUnsafeSchemes() throws {
        let url = try NavigationURL.parse("vl://watch/1?token=secret#frag")
        let shown = NavigationURL.display(url)
        XCTAssertEqual(shown, "vl://watch/1")
        XCTAssertFalse(shown.contains("secret"))
        XCTAssertThrowsError(try NavigationURL.parse("javascript:alert(1)"))
        XCTAssertThrowsError(try NavigationURL.parse("file:///etc/passwd"))
        XCTAssertThrowsError(try NavigationURL.parse(""))
    }
}

private final class SampleDRMProvider: PlaybackDRMProviding {
    var notes: [PlaybackDRMNote] = []

    func playbackDRMDrain() -> [PlaybackDRMNote] {
        let copy = notes
        notes = []
        return copy
    }
}
