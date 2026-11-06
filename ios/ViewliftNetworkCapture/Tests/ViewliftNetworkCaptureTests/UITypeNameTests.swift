@testable import ViewliftNetworkCapture
import XCTest

final class UITypeNameTests: XCTestCase {
    func testModuleAndShortKeepsHostingGeneric() {
        let split = UITypeName.moduleAndShort(
            qualified: "UIKit.UIHostingController<App.PlayerScreen>",
            described: "UIHostingController<PlayerScreen>"
        )
        XCTAssertEqual(split.module, "UIKit")
        XCTAssertEqual(split.short, "UIHostingController<PlayerScreen>")
    }

    func testModuleAndShortForUIKitClass() {
        let split = UITypeName.moduleAndShort(qualified: "UIKit.UILabel", described: "UILabel")
        XCTAssertEqual(split.module, "UIKit")
        XCTAssertEqual(split.short, "UILabel")
    }

    func testAppViewNameSkipsSwiftUIWrappers() {
        let hosted = "_UIHostingView<ModifiedContent<PlayerScreen, _FrameLayout>>"
        XCTAssertEqual(UITypeName.appViewName(in: hosted), "PlayerScreen")
        let controller = "UIHostingController<MyApp.PlayerScreen>"
        XCTAssertEqual(UITypeName.appViewName(in: controller), "PlayerScreen")
        XCTAssertNil(UITypeName.appViewName(in: "UILabel"))
    }

    func testRoleMarksUIKitInsideSwiftUI() {
        XCTAssertEqual(
            UITypeName.role(selfIsHost: true, ancestorIsHost: false, controllerIsHost: true),
            "swiftui"
        )
        XCTAssertEqual(
            UITypeName.role(selfIsHost: false, ancestorIsHost: true, controllerIsHost: true),
            "uikit-in-swiftui"
        )
        XCTAssertEqual(
            UITypeName.role(selfIsHost: false, ancestorIsHost: false, controllerIsHost: false),
            "uikit"
        )
    }

    func testHostingMarkers() {
        XCTAssertTrue(UITypeName.isSwiftUIHost("UIHostingController<Home>"))
        XCTAssertTrue(UITypeName.isSwiftUIHost("_UIHostingView<AnyView>"))
        XCTAssertFalse(UITypeName.isSwiftUIHost("UILabel"))
    }
}
