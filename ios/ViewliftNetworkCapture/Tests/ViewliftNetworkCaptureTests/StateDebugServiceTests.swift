import Foundation
@testable import ViewliftNetworkCapture
import XCTest

final class StateDebugServiceTests: XCTestCase {
    func testFilePayloadKeepsTextAndBinaryPlistBytes() throws {
        let text = try StateDebugService.filePayload(path: "note.txt", data: Data("hello".utf8))
        XCTAssertEqual(text["encoding"], .string("utf8"))
        XCTAssertEqual(text["content"], .string("hello"))

        let disguised = Data("bplist00hello".utf8)
        let hidden = try StateDebugService.filePayload(
            path: "Library/Preferences/app.plist",
            data: disguised
        )
        XCTAssertEqual(hidden["encoding"], .string("base64"))

        let plist = try PropertyListSerialization.data(
            fromPropertyList: ["name": "viewlift"],
            format: .binary,
            options: 0
        )
        let binary = try StateDebugService.filePayload(
            path: "Library/Preferences/app.plist",
            data: plist
        )
        XCTAssertEqual(binary["encoding"], .string("base64"))
        guard case let .string(encoded) = binary["content"] else {
            return XCTFail("missing content")
        }
        XCTAssertEqual(Data(base64Encoded: encoded), plist)

        let oversized = Data(repeating: 0x61, count: StateDebugService.maxFileBytes + 1)
        XCTAssertThrowsError(try StateDebugService.filePayload(path: "big.txt", data: oversized))
    }
}
