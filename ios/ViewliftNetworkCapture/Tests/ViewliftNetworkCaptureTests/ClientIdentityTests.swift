import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class ClientIdentityTests: XCTestCase {
    private struct FixedIdentity: ClientIdentityProviding {
        let clientId: String
    }

    func testSimulatorAppsShareSimulatorUDIDAsDeviceKey() {
        let environment = [
            "SIMULATOR_UDID": "SIM-UDID-1",
            "SIMULATOR_DEVICE_NAME": "iPhone 16",
            "SIMULATOR_MODEL_IDENTIFIER": "iPhone17,3"
        ]
        let first = DeviceIdentity.resolve(
            environment: environment, vendorId: "A", deviceName: "host", hardwareModel: "arm64"
        )
        let second = DeviceIdentity.resolve(
            environment: environment, vendorId: "B", deviceName: "host", hardwareModel: "arm64"
        )
        XCTAssertEqual(first.deviceKey, "SIM-UDID-1")
        XCTAssertEqual(first.deviceKey, second.deviceKey)
        XCTAssertTrue(first.isSimulator)
        XCTAssertEqual(first.deviceName, "iPhone 16")
        XCTAssertEqual(first.deviceModel, "iPhone17,3")
    }

    func testPhysicalDeviceUsesVendorIdentifier() {
        let device = DeviceIdentity.resolve(
            environment: [:], vendorId: "VENDOR", deviceName: "iPhone", hardwareModel: "iPhone15,2"
        )
        XCTAssertEqual(device.deviceKey, "vendor:VENDOR")
        XCTAssertFalse(device.isSimulator)
        let hinted = DeviceIdentity.resolve(
            environment: [:], vendorId: nil, deviceName: "iPhone", hardwareModel: "iPhone15,2"
        )
        XCTAssertEqual(hinted.deviceKey, "hint:iPhone15,2|iPhone")
    }

    func testDeviceHintIsStablePerBootAndEmptyWithoutBootTime() {
        let first = DeviceIdentity.deviceHint(hardwareModel: "iPhone15,2", osVersion: "18.0.0", bootTime: 100)
        let again = DeviceIdentity.deviceHint(hardwareModel: "iPhone15,2", osVersion: "18.0.0", bootTime: 100)
        let rebooted = DeviceIdentity.deviceHint(hardwareModel: "iPhone15,2", osVersion: "18.0.0", bootTime: 200)
        XCTAssertEqual(first.count, 16)
        XCTAssertEqual(first, again)
        XCTAssertNotEqual(first, rebooted)
        XCTAssertEqual(DeviceIdentity.deviceHint(hardwareModel: "x", osVersion: "1", bootTime: 0), "")
        XCTAssertGreaterThan(DeviceIdentity.bootTime(), 0)
        let device = DeviceIdentity.resolve(
            environment: [:], vendorId: "V", deviceName: "iPhone", hardwareModel: "m", deviceHint: first
        )
        XCTAssertEqual(device.deviceHint, first)
    }

    func testClientIdPersistsPerDefaultsDomain() throws {
        let suite = "viewlift.identity.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let first = UserDefaultsClientIdentity(defaults: defaults)
        let second = UserDefaultsClientIdentity(defaults: defaults)
        XCTAssertEqual(first.clientId, second.clientId)
        XCTAssertFalse(first.clientId.isEmpty)
    }

    func testRegistrationEncodesDeviceIdentity() throws {
        let device = DeviceIdentity(
            deviceKey: "SIM-1", deviceName: "iPhone 16", deviceModel: "iPhone17,3", isSimulator: true
        )
        let registration = DebugClientRegistration(
            clientId: "client-a",
            appName: "App A",
            bundleId: "com.example.a",
            platform: "iOS",
            osVersion: "18.0",
            capabilities: [:],
            device: device
        )
        let data = try JSONEncoder().encode(registration)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["device_key"] as? String, "SIM-1")
        XCTAssertEqual(json["device_name"] as? String, "iPhone 16")
        XCTAssertEqual(json["is_simulator"] as? Bool, true)
        XCTAssertEqual(json["device_hint"] as? String, "")
        XCTAssertEqual(json["sdk_version"] as? String, DeviceIdentity.sdkVersion)
    }

    func testReportCaptureSessionDropsRawEmail() {
        let raw = CaptureSessionIdentity(
            authState: "logged_in",
            userId: "user-1",
            emailMask: "ada@viewlift.com"
        )
        XCTAssertEqual(raw.normalized().emailMask, "")
        let masked = CaptureSessionIdentity(
            authState: "logged_in",
            userId: "user-1",
            emailMask: "a***@viewlift.com",
            profileId: "profile-1",
            loginProvider: "apple"
        )
        XCTAssertEqual(masked.normalized(), masked)
        let guest = CaptureSessionIdentity(authState: "guest", userId: "drop-me", anonymousId: "anon-1")
        XCTAssertEqual(guest.normalized().userId, "")
        XCTAssertEqual(guest.normalized().anonymousId, "anon-1")
        reportCaptureSession(masked)
        XCTAssertEqual(CaptureSessionStore.shared.currentIdentity(), masked)
        XCTAssertEqual(CaptureSessionStore.shared.peekPending(), masked)
        CaptureSessionStore.shared.ack(masked)
        XCTAssertNil(CaptureSessionStore.shared.peekPending())
        XCTAssertEqual(masked.eventPayload()["email_mask"], .string("a***@viewlift.com"))
        XCTAssertEqual(raw.eventPayload()["email_mask"], .string(""))
    }

    func testPairPayloadCarriesClientId() {
        let transport = PinnedHTTPSTransport(identity: FixedIdentity(clientId: "client-a"))
        let payload = transport.pairPayload(pairingToken: "token")
        XCTAssertEqual(payload["pairing_token"], "token")
        XCTAssertEqual(payload["client_id"], "client-a")
    }
}
