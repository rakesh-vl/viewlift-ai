import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class FatalCrashTests: XCTestCase {
    private let sample = """
    VLC1
    sig 5 0 0x1028e17a0
    time 1794135568
    pid 43719
    arch arm64
    thread 52021665 1
    frame 0x1028e17a0
    frame 0x1028e1790
    frame 0x18a1244e4
    image 0x1028e0000 0x102924000 6cf15444ac7c39279391d8db427a5658 /private/tmp/OneApp.app/OneApp
    image 0x18a0cf000 0x22d5b4000 957f93b3880539c79c51edd1715f550e /usr/lib/system/libdyld.dylib
    image 0x18a07c000 0x22d5b4000 03bd9e32cf0a37b0898a3ce8de06d842 /usr/lib/libobjc.A.dylib
    end
    """

    func testParsesSignalFramesAndImages() throws {
        let record = try XCTUnwrap(CrashRecordParser.parse(sample))
        XCTAssertEqual(record.signalNumber, 5)
        XCTAssertEqual(record.processId, 43_719)
        XCTAssertEqual(record.arch, "arm64")
        XCTAssertTrue(record.isMainThread)
        XCTAssertEqual(record.frames, [0x1028_e17a0, 0x1028_e1790, 0x18a1_244e4])
        XCTAssertEqual(record.images.count, 3)
        XCTAssertEqual(record.images[0].uuid, "6CF15444-AC7C-3927-9391-D8DB427A5658")
        XCTAssertEqual(record.images[0].name, "OneApp")
    }

    func testTruncatedRecordKeepsTheFramesThatWereWritten() throws {
        let cut = "VLC1\nsig 11 2 0x8\nframe 0x1000\nframe 0x20"
        let record = try XCTUnwrap(CrashRecordParser.parse(cut))
        XCTAssertEqual(record.signalNumber, 11)
        XCTAssertEqual(record.frames, [0x1000, 0x20])
    }

    func testRejectsFilesThatAreNotRecords() {
        XCTAssertNil(CrashRecordParser.parse(""))
        XCTAssertNil(CrashRecordParser.parse("hello\nworld"))
        XCTAssertNil(CrashRecordParser.parse("VLC1\ntime 12"))
    }

    func testEventCarriesWhatTheMacNeedsToSymbolicate() throws {
        let record = try XCTUnwrap(CrashRecordParser.parse(sample))
        let event = CrashEventBuilder.event(from: record, executable: "OneApp", id: "abc")
        XCTAssertEqual(event["kind"], .string("fatal"))
        XCTAssertEqual(event["signal"], .string("SIGTRAP"))
        XCTAssertEqual(event["pid"], .number(43_719))
        XCTAssertEqual(event["previous_session"], .bool(true))
        guard case let .array(frames)? = event["frames"], case let .object(first) = frames[0] else {
            return XCTFail("frames missing")
        }
        XCTAssertEqual(first["image"], .string("OneApp"))
        XCTAssertEqual(first["image_uuid"], .string("6CF15444-AC7C-3927-9391-D8DB427A5658"))
        XCTAssertEqual(first["image_base"], .string("0x1028e0000"))
        XCTAssertEqual(first["offset"], .number(Double(0x17a0)))
    }

    func testFrameBelongsToTheClosestImageBase() throws {
        // The shared cache reports a huge end address for every library, so ranges overlap.
        let record = try XCTUnwrap(CrashRecordParser.parse(sample))
        let event = CrashEventBuilder.event(from: record, executable: "OneApp")
        guard case let .array(frames)? = event["frames"], case let .object(last) = frames[2] else {
            return XCTFail("frames missing")
        }
        XCTAssertEqual(last["image"], .string("libdyld.dylib"))
    }

    func testStorePromotesPendingRecordAndForgetsDeliveredOnes() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("vl-crash-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = CrashStore(directory: directory)
        try sample.write(to: store.pendingURL, atomically: true, encoding: .utf8)

        let promoted = try XCTUnwrap(store.promotePendingRecord(executable: "OneApp"))

        XCTAssertFalse(FileManager.default.fileExists(atPath: store.pendingURL.path))
        XCTAssertEqual(store.unsent().map(\.id), [promoted.id])
        XCTAssertEqual(store.unsent().first?.event["pid"], .number(43_719))
        store.remove(id: promoted.id)
        XCTAssertTrue(store.unsent().isEmpty)
    }

    func testEmptyPendingFileMeansTheLastRunDidNotCrash() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("vl-crash-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = CrashStore(directory: directory)
        try Data().write(to: store.pendingURL)

        XCTAssertNil(store.promotePendingRecord(executable: "OneApp"))
        XCTAssertTrue(store.unsent().isEmpty)
    }

    func testUnsentListIsCappedToTheNewestCrashes() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("vl-crash-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = CrashStore(directory: directory)
        for index in 0..<(CrashStore.maxUnsent + 3) {
            store.save(CrashStore.Entry(id: "id-\(index)", event: ["n": .number(Double(index))]))
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertEqual(store.unsent().count, CrashStore.maxUnsent)
        XCTAssertNil(store.unsent().first { $0.id == "id-0" })
    }

    func testUncaughtExceptionEventKeepsNameReasonAndAddresses() {
        let exception = NSException(name: .invalidArgumentException, reason: "boom", userInfo: nil)
        let entry = FatalCrashCoordinator.entry(for: exception)
        XCTAssertEqual(entry.event["kind"], .string("exception"))
        XCTAssertEqual(entry.event["name"], .string(NSExceptionName.invalidArgumentException.rawValue))
        XCTAssertEqual(entry.event["reason"], .string("boom"))
        XCTAssertEqual(entry.event["previous_session"], .bool(false))
    }
}
