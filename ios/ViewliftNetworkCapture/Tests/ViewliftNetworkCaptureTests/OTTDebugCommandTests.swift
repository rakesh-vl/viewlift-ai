import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class HostOTT: OTTDebugSnapshotProviding, OTTDebugCommandHandling {
    var performed: [String] = []

    var ottDebugProviderName: String { "AppCMS" }

    func ottDebugSnapshot() -> [String: JSONValue] {
        ["provider": .string("AppCMS")]
    }

    func ottDebugSections() -> [String] { ["config", "session"] }

    func ottDebugSection(_ name: String, arguments: [String: JSONValue]) async -> [String: JSONValue]? {
        guard name == "config" else { return nil }
        var body: [String: JSONValue] = ["site_id": .string("site-1")]
        if let echo = arguments["echo"] { body["echo"] = echo }
        return body
    }

    func ottDebugDiagnostics() async -> [JSONValue] {
        [.object(["id": .string("stale_login"), "severity": .string("warning")])]
    }

    func ottDebugActions() -> [String] { ["refresh_token"] }

    func ottDebugPerform(_ action: String, payload: [String: JSONValue]) async throws -> [String: JSONValue] {
        performed.append(action)
        return ["ok": .bool(true)]
    }
}

final class HandlerOnlyOTT: OTTDebugCommandHandling {
    var ottDebugProviderName: String { "AppCMS" }
    func ottDebugSections() -> [String] { ["config"] }
    func ottDebugActions() -> [String] { [] }
    func ottDebugSection(_ name: String, arguments: [String: JSONValue]) async -> [String: JSONValue]? {
        name == "config" ? ["site_id": .string("site-2")] : nil
    }
    func ottDebugDiagnostics() async -> [JSONValue] { [] }
    func ottDebugPerform(_ action: String, payload: [String: JSONValue]) async throws -> [String: JSONValue] { [:] }
}

final class OTTDebugCommandTests: XCTestCase {
    private let host = HostOTT()

    override func tearDown() {
        OTTDebugRegistry.clear()
        super.tearDown()
    }

    private func command(_ name: String, _ payload: [String: JSONValue] = [:]) -> DebugCommand {
        DebugCommand(id: UUID().uuidString, name: name, payload: payload, expiresAt: Date().timeIntervalSince1970 + 30)
    }

    func testSectionsListHandlerData() async throws {
        OTTDebugRegistry.register(host)
        let result = try await ottCommand(command("ott.sections"))
        XCTAssertEqual(result["provider"], .string("AppCMS"))
        XCTAssertEqual(result["sections"], .array([.string("config"), .string("session")]))
        XCTAssertEqual(result["actions"], .array([.string("refresh_token")]))
    }

    func testSectionReturnsWrappedData() async throws {
        OTTDebugRegistry.register(host)
        let result = try await ottCommand(command("ott.section", ["name": .string("config")]))
        XCTAssertEqual(result["section"], .string("config"))
        XCTAssertEqual(result["data"], .object(["site_id": .string("site-1")]))
    }

    func testSectionForwardsArguments() async throws {
        OTTDebugRegistry.register(host)
        let result = try await ottCommand(command("ott.section", [
            "name": .string("config"),
            "args": .object(["echo": .string("v1")])
        ]))
        XCTAssertEqual(result["data"], .object(["site_id": .string("site-1"), "echo": .string("v1")]))
    }

    func testUnknownSectionReportsError() async throws {
        OTTDebugRegistry.register(host)
        let result = try await ottCommand(command("ott.section", ["name": .string("nope")]))
        XCTAssertEqual(result["error"], .string("unknown_section"))
    }

    func testSectionWithoutNameIsInvalid() async {
        OTTDebugRegistry.register(host)
        do {
            _ = try await ottCommand(command("ott.section"))
            XCTFail("expected invalid payload")
        } catch {
            XCTAssertTrue(error is DebugBridgeError)
        }
    }

    func testNoHandlerReportsError() async throws {
        let result = try await ottCommand(command("ott.diagnostics"))
        XCTAssertEqual(result["error"], .string("no_handler"))
    }

    func testDiagnosticsCountFindings() async throws {
        OTTDebugRegistry.register(host)
        let result = try await ottCommand(command("ott.diagnostics"))
        XCTAssertEqual(result["count"], .number(1))
    }

    func testActionRunsOnlyWhitelistedNames() async throws {
        OTTDebugRegistry.register(host)
        let ok = try await ottCommand(command("ott.action", ["name": .string("refresh_token")]))
        XCTAssertEqual(ok["ok"], .bool(true))
        XCTAssertEqual(ok["action"], .string("refresh_token"))
        let rejected = try await ottCommand(command("ott.action", ["name": .string("wipe")]))
        XCTAssertEqual(rejected["error"], .string("unknown_action"))
        XCTAssertEqual(host.performed, ["refresh_token"])
    }

    func testSnapshotMergesHandlerSectionsWithoutProvider() async throws {
        let handlerOnly = HandlerOnlyOTT()
        OTTDebugRegistry.register(handler: handlerOnly)
        let result = try await ottCommand(command("ott.snapshot"))
        XCTAssertEqual(result["provider"], .string("AppCMS"))
        XCTAssertEqual(result["config"], .object(["site_id": .string("site-2")]))
    }

    func testChangedSectionsAreDebounced() {
        let start = Date()
        OTTDebugRegistry.notifyChanged(section: "session")
        OTTDebugRegistry.notifyChanged(section: "config")
        XCTAssertEqual(OTTDebugRegistry.drainChanged(now: start), ["config", "session"])
        OTTDebugRegistry.notifyChanged(section: "session")
        XCTAssertNil(OTTDebugRegistry.drainChanged(now: start.addingTimeInterval(0.1)))
        XCTAssertEqual(OTTDebugRegistry.drainChanged(now: start.addingTimeInterval(1)), ["session"])
    }

    func testDisabledModuleRejectsOTTCommands() async {
        let config = CaptureConfiguration(
            receiverURL: URL(string: "https://127.0.0.1:9")!,
            pinnedCertificateSHA256: "",
            debugModules: [.preview]
        )
        let executor = DebugCommandExecutor(performance: PerformanceCollector())
        do {
            _ = try await executor.execute(command("ott.sections"), config: config)
            XCTFail("expected module disabled")
        } catch {
            XCTAssertEqual(error as? DebugBridgeError, .moduleDisabled)
        }
    }
}
