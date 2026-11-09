import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class OTTDebugHostTests: XCTestCase {
    override func tearDown() {
        OTTDebugRegistry.clear()
        super.tearDown()
    }

    private func command(_ name: String, _ payload: [String: JSONValue] = [:]) -> DebugCommand {
        DebugCommand(id: UUID().uuidString, name: name, payload: payload, expiresAt: Date().timeIntervalSince1970 + 30)
    }

    private func makeHost() -> OTTDebugHost {
        OTTDebugHost(provider: "PlayerSample", title: "Player")
            .section("playback", title: "Playback", group: "Player") { args in
                ["state": .string("playing"), "echo": args["echo"] ?? .null]
            }
            .action(
                "seek",
                title: "Seek",
                note: "Moves the playhead",
                fields: [OTTDebugActionField(name: "seconds", label: "Seconds", kind: .number)]
            ) { payload in
                ["seconds": payload["seconds"] ?? .null]
            }
            .diagnostic {
                OTTDebugFinding(id: "stall", severity: .warning, title: "Stalls rising")
            }
            .diagnostic { nil }
    }

    func testManifestListsPagesAndActionForms() async throws {
        let host = makeHost()
        host.register()
        let result = try await ottCommand(command("ott.sections"))
        XCTAssertEqual(result["provider"], .string("PlayerSample"))
        XCTAssertEqual(result["title"], .string("Player"))
        XCTAssertEqual(result["sections"], .array([.string("playback")]))
        let page = result["pages"]?.arrayValue?.first
        XCTAssertEqual(page?["title"], .string("Playback"))
        XCTAssertEqual(page?["group"], .string("Player"))
        let field = result["action_forms"]?["seek"]?.arrayValue?.first
        XCTAssertEqual(field?["kind"], .string("number"))
        XCTAssertEqual(result["action_notes"]?["seek"], .string("Moves the playhead"))
    }

    func testSectionRunsBuilderWithArguments() async throws {
        let host = makeHost()
        host.register()
        let result = try await ottCommand(command("ott.section", [
            "name": .string("playback"),
            "args": .object(["echo": .string("x")])
        ]))
        XCTAssertEqual(result["data"]?["state"], .string("playing"))
        XCTAssertEqual(result["data"]?["echo"], .string("x"))
    }

    func testDiagnosticsKeepOnlyFindings() async throws {
        let host = makeHost()
        host.register()
        let result = try await ottCommand(command("ott.diagnostics"))
        XCTAssertEqual(result["count"], .number(1))
        XCTAssertEqual(result["findings"]?.arrayValue?.first?["severity"], .string("warning"))
    }

    func testActionRunsRegisteredRunnerOnly() async throws {
        let host = makeHost()
        host.register()
        let ok = try await ottCommand(command("ott.action", [
            "name": .string("seek"),
            "payload": .object(["seconds": .number(30)])
        ]))
        XCTAssertEqual(ok["seconds"], .number(30))
        let rejected = try await ottCommand(command("ott.action", ["name": .string("wipe")]))
        XCTAssertEqual(rejected["error"], .string("unknown_action"))
    }

    func testObservedNotificationMarksSectionsChanged() {
        let host = makeHost()
        let name = Notification.Name("OTTDebugHostTests.changed")
        host.observe(name, sections: ["playback"])
        NotificationCenter.default.post(name: name, object: nil)
        XCTAssertEqual(OTTDebugRegistry.drainChanged(now: Date().addingTimeInterval(5)), ["playback"])
    }

    func testNamesOnlyHandlerHasNoManifestKeys() async throws {
        let handler = HandlerOnlyOTT()
        OTTDebugRegistry.register(handler: handler)
        let result = try await ottCommand(command("ott.sections"))
        withExtendedLifetime(handler) {}
        XCTAssertNil(result["pages"])
        XCTAssertEqual(result["sections"], .array([.string("config")]))
    }
}

final class OTTDebugRedactorTests: XCTestCase {
    private func jwt(_ claims: String) -> String {
        let body = Data(claims.utf8).base64EncodedString()
            .replacingOccurrences(of: "=", with: "")
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
        return "eyJhbGciOiJIUzI1NiJ9.\(body).sig"
    }

    func testTokenReturnsMetadataOnly() {
        let raw = jwt(#"{"exp":100,"sub":"user-1","iss":"vl"}"#)
        let result = OTTDebugRedactor.token(raw, now: Date(timeIntervalSince1970: 40))
        XCTAssertEqual(result["present"], .bool(true))
        XCTAssertEqual(result["expires_in_sec"], .number(60))
        XCTAssertEqual(result["expired"], .bool(false))
        XCTAssertEqual(result["jwt"]?["iss"], .string("vl"))
        XCTAssertNil(result["jwt"]?["sub"])
        XCTAssertFalse(String(describing: result).contains(raw))
    }

    func testScrubMasksSecretKeysAndUrlQueries() {
        let value = OTTDebugValue.safe([
            "licenseToken": "abc",
            "pinEnabled": true,
            "stream": "https://cdn.example.com/a/master.m3u8?sig=1",
            "nested": ["xApiKey": 12]
        ] as [String: Any])
        XCTAssertEqual(value["licenseToken"]?["present"], .bool(true))
        XCTAssertEqual(value["pinEnabled"], .bool(true))
        XCTAssertEqual(value["stream"], .string("https://cdn.example.com/a/master.m3u8"))
        XCTAssertEqual(value["nested"]?["xApiKey"], .string("[redacted]"))
    }

    func testEmailKeepsDomainOnly() {
        XCTAssertEqual(OTTDebugRedactor.email("rakesh@viewlift.com"), .string("r***@viewlift.com"))
    }

    func testValueKeepsBoolsDistinctFromNumbers() {
        XCTAssertEqual(OTTDebugValue.from(true), .bool(true))
        XCTAssertEqual(OTTDebugValue.from(3), .number(3))
    }
}

final class OTTDebugRecorderTests: XCTestCase {
    func testRingBufferDropsOldest() {
        let buffer = OTTDebugRingBuffer<Int>(capacity: 2)
        [1, 2, 3].forEach(buffer.append)
        XCTAssertEqual(buffer.elements, [2, 3])
    }

    func testRecorderCountsAndRedactsUserInfo() {
        let recorder = OTTDebugNotificationRecorder(capacity: 5)
        let name = Notification.Name("OTTDebugRecorderTests.event")
        recorder.start([name])
        NotificationCenter.default.post(name: name, object: nil, userInfo: ["authToken": "secret", "position": 12])
        recorder.stop()
        let entry = recorder.entries().first
        XCTAssertEqual(entry?["name"], .string(name.rawValue))
        XCTAssertEqual(entry?["info"]?["position"], .number(12))
        XCTAssertEqual(entry?["info"]?["authToken"]?["present"], .bool(true))
        XCTAssertEqual(recorder.counts()[name.rawValue], .number(1))
    }
}
