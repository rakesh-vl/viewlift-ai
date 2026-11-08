import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class ReceiverDiscoveryTests: XCTestCase {
    func testReceiverDiscoveryReplacesStaleWifiAddress() {
        XCTAssertTrue(ReceiverAddress.shouldAdopt(currentHost: "192.168.0.39", discovered: "10.117.22.7"))
        XCTAssertFalse(ReceiverAddress.shouldAdopt(currentHost: "10.117.22.7", discovered: "10.117.22.7"))
        XCTAssertFalse(ReceiverAddress.shouldAdopt(currentHost: "10.117.22.7", discovered: "169.254.140.52"))
        #if targetEnvironment(simulator)
        XCTAssertTrue(ReceiverAddress.shouldAdopt(currentHost: nil, discovered: "127.0.0.1"))
        #else
        XCTAssertFalse(ReceiverAddress.shouldAdopt(currentHost: nil, discovered: "127.0.0.1"))
        #endif
        let record = DiscoveredReceiver.parse(
            ip: "10.117.22.7",
            portText: "8743",
            fingerprint: "abc",
            debugKey: "key"
        )
        XCTAssertEqual(record?.host, "10.117.22.7")
        XCTAssertEqual(record?.port, 8743)
        XCTAssertNil(
            DiscoveredReceiver.parse(ip: "10.117.22.7", portText: "8743", fingerprint: "", debugKey: "key")
        )
    }
}
