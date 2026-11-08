import AVFoundation
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
@testable import ViewliftNetworkCapture
import XCTest

final class CrashReplayTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("vl-replay-\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    func testRingOverwritesTheOldestSlotAndKeepsTimeOrder() throws {
        let ring = CrashReplayRing(directory: root, capacity: 3)
        for index in 0..<5 {
            ring.append(jpeg: try Self.jpeg(shade: index), frame: Self.slot(time: 100 + Double(index)))
        }
        let slots = CrashReplayRing.load(from: root)
        XCTAssertEqual(slots.map(\.time), [102, 103, 104])
        XCTAssertEqual(slots.map(\.slot), [2, 0, 1])
    }

    func testFreezeMovesTheRingAndDescribesIt() throws {
        let store = CrashStore(directory: root)
        let ring = CrashReplayRing(directory: store.replayDirectory)
        ring.append(jpeg: try Self.jpeg(shade: 1), frame: Self.slot(time: 10))
        ring.append(jpeg: try Self.jpeg(shade: 2), frame: Self.slot(time: 12.5))
        let context = try JSONEncoder().encode(["captured_at": JSONValue.number(12)])
        try context.write(to: store.replayDirectory.appendingPathComponent(CrashReplayRing.contextName))

        let frozen = store.freezeReplay(id: "crash-1")

        XCTAssertFalse(FileManager.default.fileExists(atPath: store.replayDirectory.path))
        XCTAssertEqual(store.replayFolders().map(\.lastPathComponent), ["crash-1"])
        XCTAssertEqual(frozen.context?["captured_at"], .number(12))
        XCTAssertEqual(frozen.replay?["frames"], .number(2))
        XCTAssertEqual(frozen.replay?["duration_ms"], .number(2_500))
        XCTAssertEqual(frozen.replay?["status"], .string("pending"))
    }

    func testFreezeWithoutFramesStillKeepsTheContext() throws {
        let store = CrashStore(directory: root)
        try FileManager.default.createDirectory(at: store.replayDirectory, withIntermediateDirectories: true)
        let context = try JSONEncoder().encode(["captured_at": JSONValue.number(3)])
        try context.write(to: store.replayDirectory.appendingPathComponent(CrashReplayRing.contextName))

        let frozen = store.freezeReplay(id: "crash-2")

        XCTAssertNotNil(frozen.context)
        XCTAssertNil(frozen.replay)
    }

    func testOnlyTheNewestReplaysAreKept() throws {
        let store = CrashStore(directory: root)
        for index in 0..<(CrashStore.maxReplays + 2) {
            let ring = CrashReplayRing(directory: store.replayDirectory)
            ring.append(jpeg: try Self.jpeg(shade: index), frame: Self.slot(time: Double(index)))
            _ = store.freezeReplay(id: "crash-\(index)")
            Thread.sleep(forTimeInterval: 0.02)
        }
        XCTAssertEqual(store.replayFolders().count, CrashStore.maxReplays)
        XCTAssertNil(store.replayFolders().first { $0.lastPathComponent == "crash-0" })
    }

    func testEncoderWritesAPlayableVideoThatEndsAtTheCrash() async throws {
        let ring = CrashReplayRing(directory: root)
        let touch = CrashReplayTouch(phase: "began", pointX: 0.5, pointY: 0.5, label: "Play")
        for index in 0..<8 {
            var frame = Self.slot(time: 1_000 + Double(index) * 0.25)
            if index == 4 { frame.touches = [touch] }
            ring.append(jpeg: try Self.jpeg(shade: index), frame: frame)
        }

        let video = try CrashReplayEncoder.encode(folder: root, crashTime: 1_002.5)

        XCTAssertEqual(video.frames, 8)
        XCTAssertEqual(video.durationSeconds, 2.5 + CrashReplayEncoder.crashHoldSeconds, accuracy: 0.01)
        let asset = AVURLAsset(url: video.url)
        let duration = try await asset.load(.duration)
        XCTAssertEqual(duration.seconds, video.durationSeconds, accuracy: 0.1)
        let tracks = try await asset.loadTracks(withMediaType: .video)
        XCTAssertEqual(tracks.count, 1)
    }

    func testEncoderRefusesAnEmptyFolder() throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        XCTAssertThrowsError(try CrashReplayEncoder.encode(folder: root, crashTime: nil)) { error in
            XCTAssertEqual(error as? CrashReplayEncoderError, .noFrames)
        }
    }

    func testCaptionCountsDownToTheCrash() {
        var slot = Self.slot(time: 10)
        slot.title = "Home"
        XCTAssertEqual(CrashReplayEncoder.caption(slot: slot, crashTime: 13.25), "-3.2s  PlayerViewController · Home")
        XCTAssertEqual(CrashReplayEncoder.caption(slot: slot, crashTime: nil), "PlayerViewController · Home")
    }

    func testChunksCoverTheFileExactlyAndFitTheEventLimit() {
        let data = Data((0..<500_001).map { UInt8($0 % 251) })
        let total = CrashReplayChunker.chunkCount(byteCount: data.count)
        XCTAssertEqual(total, 3)
        let joined = (0..<total).reduce(into: Data()) { $0.append(CrashReplayChunker.chunk(data, index: $1)) }
        XCTAssertEqual(joined, data)
        XCTAssertLessThanOrEqual(CrashReplayChunker.chunk(data, index: 0).base64EncodedString().utf8.count, 300_000)
        XCTAssertEqual(CrashReplayChunker.chunk(data, index: 3), Data())
        XCTAssertEqual(CrashReplayChunker.chunkCount(byteCount: 0), 0)
    }

    func testTimelineMeasuresFramesFromTheCrash() throws {
        let ring = CrashReplayRing(directory: root)
        ring.append(jpeg: try Self.jpeg(shade: 0), frame: Self.slot(time: 50))
        ring.append(jpeg: try Self.jpeg(shade: 1), frame: Self.slot(time: 51))
        let rows = CrashReplayUploader.timeline(folder: root, crash: ["timestamp": .number(52)])
        guard case let .object(first)? = rows.first else { return XCTFail("no timeline") }
        XCTAssertEqual(first["offset_ms"], .number(0))
        XCTAssertEqual(first["before_crash_ms"], .number(2_000))
        XCTAssertNotNil(CrashReplayUploader.poster(folder: root))
    }

    func testContextKeepsRequestsWithoutQueryStrings() {
        let exchange = CapturedHTTPExchange(
            url: "https://api.example.com/v1/items?token=abc",
            method: "GET",
            host: "api.example.com",
            path: "/v1/items?token=abc",
            status: 500,
            durationMs: 42,
            protocolName: "h2",
            requestHeaders: [:],
            requestBodyBase64: nil,
            responseHeaders: [:],
            responseBodyBase64: nil,
            error: nil,
            requestId: nil,
            contentType: nil,
            source: "test",
            extraRedactionKeys: []
        )
        CrashContextWriter.shared.noteRequest(exchange)
        guard case let .array(rows)? = CrashContextWriter.shared.snapshot()["requests"],
              case let .object(last)? = rows.last else {
            return XCTFail("no requests")
        }
        XCTAssertEqual(last["path"], .string("/v1/items"))
        XCTAssertEqual(last["status"], .number(500))
    }

    private static func slot(time: TimeInterval) -> CrashReplaySlot {
        CrashReplaySlot(
            slot: 0, time: time, width: 64, height: 128,
            screen: "PlayerViewController", title: "", touches: []
        )
    }

    private static func jpeg(shade: Int) throws -> Data {
        let width = 64
        let height = 128
        let context = try XCTUnwrap(CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ))
        let level = CGFloat(shade % 8) / 8
        context.setFillColor(CGColor(red: level, green: 0.3, blue: 1 - level, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let image = try XCTUnwrap(context.makeImage())
        let data = NSMutableData()
        let destination = try XCTUnwrap(
            CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil)
        )
        CGImageDestinationAddImage(destination, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }
}
