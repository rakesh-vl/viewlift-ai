import Foundation
@testable import ViewliftNetworkCapture
import XCTest

private final class MemorySettingsStore: RemoteSettingsStoring {
    var saved = RemoteCaptureSettings()
    func load() -> RemoteCaptureSettings { saved }
    func save(_ settings: RemoteCaptureSettings) { saved = settings }
}

final class RemoteSettingsTests: XCTestCase {
    private func base() -> CaptureConfiguration {
        CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:9")!,
            pinnedCertificateSHA256: "",
            debugModules: [.ui, .memory, .explorer, .storage]
        )
    }

    func testOverridesCannotEnableModulesTheHostLeftOff() throws {
        let service = RemoteSettingsService(store: MemorySettingsStore())
        var applied: CaptureConfiguration?
        _ = service.bind(base: base()) { applied = $0 }
        _ = try service.update(["modules": .array([.string("ui"), .string("push")])])
        XCTAssertEqual(applied?.debugModules, [.ui])
    }

    func testUpdateAppliesPersistsAndNotifies() throws {
        let store = MemorySettingsStore()
        let service = RemoteSettingsService(store: store)
        var applied: CaptureConfiguration?
        _ = service.bind(base: base()) { applied = $0 }
        let snapshot = try service.update([
            "modules": .array([.string("explorer"), .string("storage")]),
            "capture_network": .bool(false),
            "max_response_body_bytes": .number(9_999_999),
            "body_content_types": .array([.string("application/json")])
        ])
        XCTAssertEqual(applied?.debugModules, [.explorer, .storage])
        XCTAssertEqual(applied?.captureNetwork, false)
        XCTAssertEqual(applied?.maxResponseBodyBytes, RemoteSettingsService.maxBodyBytes)
        XCTAssertEqual(store.saved.bodyContentTypes, ["application/json"])
        XCTAssertEqual(snapshot["overridden"], .bool(true))

        let restored = RemoteSettingsService(store: store).bind(base: base())
        XCTAssertEqual(restored.debugModules, [.explorer, .storage])

        _ = try service.update(["reset": .bool(true)])
        XCTAssertEqual(store.saved, RemoteCaptureSettings())
        XCTAssertEqual(applied?.debugModules, [.ui, .memory, .explorer, .storage])
    }

    func testRejectsUnknownModulesAndBadTypes() {
        let service = RemoteSettingsService(store: MemorySettingsStore())
        _ = service.bind(base: base())
        XCTAssertThrowsError(try service.update(["modules": .array([.string("root")])]))
        XCTAssertThrowsError(try service.update(["capture_network": .string("yes")]))
    }

    func testBodyContentTypeFilter() {
        var config = base()
        XCTAssertTrue(config.keepsBody(contentType: nil))
        config.bodyContentTypes = ["application/json"]
        XCTAssertTrue(config.keepsBody(contentType: "Application/JSON; charset=utf-8"))
        XCTAssertFalse(config.keepsBody(contentType: "image/png"))
        XCTAssertFalse(config.keepsBody(contentType: nil))
    }
}

final class StorageEditTests: XCTestCase {
    private let state = StateDebugService()

    func testFileKindDetection() {
        XCTAssertEqual(StorageFileKind.detect(Data("{\"a\":1}".utf8)), .json)
        XCTAssertEqual(StorageFileKind.detect(Data("SQLite format 3\u{0}rest".utf8)), .sqlite)
        XCTAssertEqual(StorageFileKind.detect(Data([0x89, 0x50, 0x4E, 0x47, 0x0D])), .image)
        XCTAssertEqual(StorageFileKind.detect(Data("plain".utf8)), .text)
        let plist = try? PropertyListSerialization.data(fromPropertyList: ["k": "v"], format: .binary, options: 0)
        XCTAssertEqual(plist.flatMap(StorageFileKind.plistJSON), .object(["k": .string("v")]))
    }

    func testRenameAndDeleteStayInsideSandbox() throws {
        let folder = "Library/Caches/viewlift-tests-\(UUID().uuidString)"
        let root = try state.sandboxURL(folder)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("x".utf8).write(to: root.appendingPathComponent("a.txt"))

        let renamed = try state.renameFile(relativePath: "\(folder)/a.txt", newName: "b.txt")
        XCTAssertEqual(renamed["path"], .string("\(folder)/b.txt"))
        XCTAssertThrowsError(try state.renameFile(relativePath: "\(folder)/b.txt", newName: "../escape.txt"))
        _ = try state.deleteFile(relativePath: "\(folder)/b.txt")
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("b.txt").path))
        XCTAssertThrowsError(try state.deleteFile(relativePath: "Library"))
        XCTAssertThrowsError(try state.deleteFile(relativePath: "../outside"))
    }

    func testCookiesAreMaskedAndDeletable() throws {
        let storage = HTTPCookieStorage.sharedCookieStorage(forGroupContainerIdentifier: "viewlift-tests-\(UUID())")
        let cookie = try XCTUnwrap(HTTPCookie(properties: [
            .name: "session", .value: "abcdefghijklmnop", .domain: "example.com", .path: "/"
        ]))
        storage.setCookie(cookie)
        let listed = state.cookies(storage: storage)
        guard case let .array(rows) = listed["cookies"], case let .object(row) = rows.first else {
            return XCTFail("missing cookie")
        }
        XCTAssertEqual(row["value_preview"], .string("abcd…op"))
        let deleted = state.deleteCookie(name: "session", domain: "example.com", path: "/", storage: storage)
        XCTAssertEqual(deleted["deleted"], .number(1))
        XCTAssertTrue(storage.cookies?.isEmpty ?? true)
    }
}
