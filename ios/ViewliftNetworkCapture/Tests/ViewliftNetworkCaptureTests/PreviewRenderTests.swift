import Foundation
import SwiftUI
@testable import ViewliftNetworkCapture
import XCTest

final class PreviewRenderTests: XCTestCase {
    override func setUp() {
        super.setUp()
        PreviewRegistry.shared.removeAll()
    }

    override func tearDown() {
        PreviewRegistry.shared.removeAll()
        super.tearDown()
    }

    private func names(_ list: [String: JSONValue]) -> [String] {
        guard case let .array(rows) = list["previews"] else { return [] }
        return rows.compactMap { row in
            guard case let .object(fields) = row, case let .string(name) = fields["name"] else { return nil }
            return name
        }
    }

    private func command(_ name: String) throws -> DebugCommand {
        let json = #"{"id":"1","name":"\#(name)","payload":{},"expires_at":0}"#
        return try JSONDecoder().decode(DebugCommand.self, from: Data(json.utf8))
    }

    func testRegisterKeepsOrderAndReplacesByName() {
        ViewliftPreviews.register("Login") { Text("Login") }
        ViewliftPreviews.register("Home", states: ["empty", "full"]) { state in Text(state) }
        ViewliftPreviews.register("Login", states: ["error"]) { state in Text(state) }
        XCTAssertEqual(names(PreviewRegistry.shared.list()), ["Login", "Home"])
        XCTAssertEqual(PreviewRegistry.shared.entry("Login")?.states, ["error"])
    }

    func testStatesAreDeduplicatedAndDefaulted() {
        ViewliftPreviews.register("Card", states: ["a", "a", "", "b"]) { state in Text(state) }
        ViewliftPreviews.register("Empty", states: []) { state in Text(state) }
        XCTAssertEqual(PreviewRegistry.shared.entry("Card")?.states, ["a", "b"])
        XCTAssertEqual(PreviewRegistry.shared.entry("Empty")?.states, [PreviewRegistry.defaultState])
    }

    func testBlankNamesAreIgnoredAndUnregisterRemoves() {
        ViewliftPreviews.register("   ") { Text("x") }
        ViewliftPreviews.register("Player") { Text("x") }
        ViewliftPreviews.unregister("Player")
        XCTAssertEqual(PreviewRegistry.shared.list()["count"], .number(0))
    }

    func testPreviewListRunsThroughTheExecutor() async throws {
        ViewliftPreviews.register("Settings") { Text("Settings") }
        let executor = DebugCommandExecutor(performance: PerformanceCollector())
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:9")!,
            pinnedCertificateSHA256: "",
            debugModules: [.preview]
        )
        let result = try await executor.execute(command("preview.list"), config: config)
        XCTAssertEqual(names(result), ["Settings"])
    }

    #if canImport(UIKit)
    @MainActor
    func testRenderRejectsUnknownPreviewAndState() async throws {
        ViewliftPreviews.register("Card", states: ["a"]) { state in Text(state) }
        let missing = try await PreviewRenderService.render(AutomationPayload(["name": .string("Nope")]))
        XCTAssertEqual(missing["ok"], .bool(false))
        XCTAssertEqual(names(missing), ["Card"])
        let badState = try await PreviewRenderService.render(
            AutomationPayload(["name": .string("Card"), "state": .string("b")])
        )
        XCTAssertEqual(badState["states"], .array([.string("a")]))
    }
    #endif

    func testPreviewCommandsNeedThePreviewModule() async throws {
        let executor = DebugCommandExecutor(performance: PerformanceCollector())
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:9")!,
            pinnedCertificateSHA256: "",
            debugModules: [.ui]
        )
        do {
            _ = try await executor.execute(command("preview.list"), config: config)
            XCTFail("expected moduleDisabled")
        } catch DebugBridgeError.moduleDisabled {
            // Expected.
        }
    }
}
