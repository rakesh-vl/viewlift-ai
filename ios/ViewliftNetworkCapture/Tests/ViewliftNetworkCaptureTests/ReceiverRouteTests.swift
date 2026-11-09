import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class ReceiverRouteTests: XCTestCase {
    func testAlternateAddressesComeFromBonjourText() {
        let record = DiscoveredReceiver.parse(
            ip: "192.168.1.10",
            portText: "8743",
            fingerprint: "abc",
            debugKey: "key",
            alternates: "100.105.93.60, 192.168.1.10,127.0.0.1,169.254.1.1,"
        )
        #if targetEnvironment(simulator)
        let usable = ["100.105.93.60", "127.0.0.1"]
        #else
        let usable = ["100.105.93.60"]
        #endif
        XCTAssertEqual(record?.alternates, usable)
        XCTAssertEqual(record?.candidateHosts, ["192.168.1.10"] + usable)
    }

    func testCandidatesRotateAndKeepPreferredHost() {
        var candidates = ReceiverCandidates()
        candidates.replace(with: ["192.168.1.10", "100.105.93.60", "192.168.1.10"])
        XCTAssertEqual(candidates.hosts, ["192.168.1.10", "100.105.93.60"])
        XCTAssertEqual(candidates.current, "192.168.1.10")
        XCTAssertEqual(candidates.rotate(), "100.105.93.60")
        XCTAssertEqual(candidates.rotate(), "192.168.1.10")
        candidates.replace(with: ["192.168.1.10", "100.105.93.60"], preferring: "100.105.93.60")
        XCTAssertEqual(candidates.current, "100.105.93.60")
    }

    func testSingleAddressNeverRotates() {
        var candidates = ReceiverCandidates()
        candidates.replace(with: ["192.168.1.10"])
        XCTAssertNil(candidates.rotate())
        XCTAssertEqual(candidates.current, "192.168.1.10")
    }

    func testRouteSwitchesAddressAfterRepeatedFailures() {
        var route = ReceiverRoute()
        route.candidates.replace(with: ["192.168.1.10", "100.105.93.60"])
        XCTAssertNil(route.recordFailure("first"))
        XCTAssertEqual(route.recordFailure("second"), "100.105.93.60")
        route.recordSuccess()
        XCTAssertNil(route.recordFailure("third"))
        XCTAssertEqual(route.takeFailures(), ["first", "second", "third"])
        XCTAssertTrue(route.takeFailures().isEmpty)
    }

    func testRouteKeepsOnlyTheLatestFailures() {
        var route = ReceiverRoute()
        for index in 0..<(ReceiverRoute.maxRecordedFailures + 4) {
            _ = route.recordFailure("failure \(index)")
        }
        XCTAssertEqual(route.recentFailures.count, ReceiverRoute.maxRecordedFailures)
        XCTAssertEqual(route.recentFailures.last, "failure \(ReceiverRoute.maxRecordedFailures + 3)")
    }

    func testOnlyConnectivityErrorsCountAsFailures() {
        XCTAssertTrue(TransportFailure.isConnectivity(URLError(.timedOut)))
        XCTAssertTrue(TransportFailure.isConnectivity(URLError(.cannotConnectToHost)))
        XCTAssertTrue(TransportFailure.isConnectivity(URLError(.networkConnectionLost)))
        XCTAssertFalse(TransportFailure.isConnectivity(URLError(.cancelled)))
        XCTAssertFalse(TransportFailure.isConnectivity(URLError(.serverCertificateUntrusted)))
        XCTAssertFalse(TransportFailure.isConnectivity(TransportError.notPaired))
    }

    func testRememberedReceiverSurvivesRelaunch() throws {
        let suite = "com.viewlift.networkcapture.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let memory = UserDefaultsReceiverMemory(defaults: defaults)
        XCTAssertNil(memory.load())
        let saved = RememberedReceiver(
            hosts: ["192.168.1.10", "100.105.93.60"],
            port: 8742,
            fingerprint: "abc",
            debugKey: "key"
        )
        memory.save(saved)
        XCTAssertEqual(UserDefaultsReceiverMemory(defaults: defaults).load(), saved)
    }

    func testTailscaleRangeIsRecognised() {
        XCTAssertTrue(ReceiverAddress.isVPN("100.105.93.60"))
        XCTAssertTrue(ReceiverAddress.isVPN("100.64.0.1"))
        XCTAssertTrue(ReceiverAddress.isVPN("100.127.255.254"))
        XCTAssertFalse(ReceiverAddress.isVPN("100.63.0.1"))
        XCTAssertFalse(ReceiverAddress.isVPN("100.128.0.1"))
        XCTAssertFalse(ReceiverAddress.isVPN("192.168.0.40"))
        XCTAssertFalse(ReceiverAddress.isVPN("100.105.93"))
        XCTAssertFalse(ReceiverAddress.isVPN("not-an-ip"))
    }

    func testVPNAddressSurvivesAnAdvertisementWithoutIt() {
        // The Mac advertised without Tailscale (it was down). The phone keeps the address it learned.
        let merged = ReceiverAddress.mergingVPN(
            into: ["192.168.0.40"],
            remembered: ["192.168.0.40", "100.105.93.60", "10.0.0.5"]
        )
        XCTAssertEqual(merged, ["192.168.0.40", "100.105.93.60"])
    }

    func testFreshVPNAddressesAreNotDuplicatedAndTheTotalIsCapped() {
        let same = ReceiverAddress.mergingVPN(
            into: ["192.168.0.40", "100.105.93.60"],
            remembered: ["100.105.93.60"]
        )
        XCTAssertEqual(same, ["192.168.0.40", "100.105.93.60"])
        let remembered = (1...9).map { "100.64.0.\($0)" }
        let capped = ReceiverAddress.mergingVPN(into: ["192.168.0.40"], remembered: remembered)
        XCTAssertEqual(capped.count, 1 + ReceiverAddress.maxRememberedVPNHosts)
        XCTAssertEqual(capped.first, "192.168.0.40")
    }

    func testMergedHostsRotateToTheTailscaleAddressOffTheWifi() {
        var route = ReceiverRoute()
        let hosts = ReceiverAddress.mergingVPN(into: ["192.168.0.40"], remembered: ["100.105.93.60"])
        route.candidates.replace(with: hosts, preferring: "192.168.0.40")
        XCTAssertNil(route.recordFailure("wifi address timed out"))
        XCTAssertEqual(route.recordFailure("wifi address timed out"), "100.105.93.60")
    }

    func testWaitingForSessionIsNotAConnectionFailure() {
        XCTAssertTrue(TransportError.notStarted.isWaitingForSession)
        XCTAssertTrue(TransportError.notPaired.isWaitingForSession)
        XCTAssertFalse(TransportError.uploadFailed(status: 401).isWaitingForSession)
    }
}
