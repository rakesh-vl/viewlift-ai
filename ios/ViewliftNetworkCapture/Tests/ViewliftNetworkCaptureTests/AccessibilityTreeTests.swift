#if canImport(UIKit)
import UIKit
@testable import ViewliftNetworkCapture
import XCTest

@MainActor
final class AccessibilityTreeTests: XCTestCase {
    private var window: UIWindow!
    private var taps = 0

    override func setUp() async throws {
        try await super.setUp()
        window = UIWindow(frame: CGRect(x: 0, y: 0, width: 320, height: 640))
        window.isHidden = false
        taps = 0
    }

    override func tearDown() async throws {
        window = nil
        try await super.tearDown()
    }

    private func addButton(_ title: String, identifier: String, primary: Bool) -> UIButton {
        let action = UIAction(title: title) { [weak self] _ in self?.taps += 1 }
        let button: UIButton
        if primary {
            button = UIButton(type: .system, primaryAction: action)
        } else {
            button = UIButton(type: .system)
            button.addAction(action, for: .touchUpInside)
        }
        button.setTitle(title, for: .normal)
        button.accessibilityIdentifier = identifier
        button.frame = CGRect(x: 20, y: 40 + CGFloat(window.subviews.count) * 60, width: 200, height: 44)
        window.addSubview(button)
        return button
    }

    func testTreeListsButtonsAndFieldsWithRoles() {
        _ = addButton("Sign In", identifier: "login.submit", primary: true)
        let field = UITextField(frame: CGRect(x: 20, y: 200, width: 200, height: 44))
        field.accessibilityIdentifier = "login.email"
        field.placeholder = "Email"
        window.addSubview(field)
        let hidden = UILabel(frame: CGRect(x: 20, y: 300, width: 200, height: 20))
        hidden.text = "Hidden"
        hidden.isHidden = true
        window.addSubview(hidden)

        let nodes = AccessibilityTreeService.nodes(in: [window])
        let button = nodes.first { $0.info.identifier == "login.submit" }
        XCTAssertEqual(button?.info.role, "button")
        XCTAssertEqual(button?.info.label, "Sign In")
        XCTAssertEqual(nodes.first { $0.info.identifier == "login.email" }?.info.role, "textfield")
        XCTAssertFalse(nodes.contains { $0.info.label == "Hidden" })
    }

    func testActivateSendsPrimaryActionAndTouchUpInside() {
        _ = addButton("Primary", identifier: "primary", primary: true)
        _ = addButton("Classic", identifier: "classic", primary: false)
        let nodes = AccessibilityTreeService.nodes(in: [window])
        let primary = try? XCTUnwrap(nodes.first { $0.info.identifier == "primary" })
        let classic = try? XCTUnwrap(nodes.first { $0.info.identifier == "classic" })
        XCTAssertEqual(primary.flatMap(UIActivation.activate), "primary_action")
        XCTAssertEqual(classic.flatMap(UIActivation.activate), "touch_up_inside")
        XCTAssertEqual(taps, 2)
    }

    func testSecureFieldValueIsNeverListed() {
        let field = UITextField(frame: CGRect(x: 20, y: 400, width: 200, height: 44))
        field.isSecureTextEntry = true
        field.text = "hunter2"
        field.accessibilityIdentifier = "login.password"
        window.addSubview(field)
        let node = AccessibilityTreeService.nodes(in: [window]).first { $0.info.identifier == "login.password" }
        XCTAssertEqual(node?.info.role, "securetextfield")
        XCTAssertEqual(node?.info.value, "")
    }

    func testCellListsItsTextAndItsButtons() {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        cell.frame = CGRect(x: 0, y: 500, width: 320, height: 60)
        cell.textLabel?.text = "Episode 1"
        cell.layoutIfNeeded()
        let more = UIButton(type: .system)
        more.setTitle("More", for: .normal)
        more.frame = CGRect(x: 260, y: 10, width: 50, height: 40)
        cell.contentView.addSubview(more)
        window.addSubview(cell)
        let nodes = AccessibilityTreeService.nodes(in: [window])
        XCTAssertEqual(nodes.first { $0.info.role == "cell" }?.info.label, "Episode 1")
        XCTAssertTrue(nodes.contains { $0.info.label == "More" && $0.info.role == "button" })
    }

    func testDisabledControlsReportDisabled() {
        let button = addButton("Off", identifier: "off", primary: false)
        button.isEnabled = false
        let node = AccessibilityTreeService.nodes(in: [window]).first { $0.info.identifier == "off" }
        XCTAssertEqual(node?.info.enabled, false)
    }

    func testScrollFinderMetricsFollowContentSize() {
        let scroll = UIScrollView(frame: CGRect(x: 0, y: 0, width: 320, height: 400))
        scroll.contentSize = CGSize(width: 320, height: 1_200)
        scroll.contentInsetAdjustmentBehavior = .never
        window.addSubview(scroll)
        let metrics = ScrollFinder.metrics(of: scroll)
        XCTAssertTrue(metrics.canScroll(.down))
        XCTAssertFalse(metrics.canScroll(.up))
        XCTAssertEqual(metrics.maxOffset.y, 800)
    }

    func testTextInputFinderFindsNestedField() {
        let container = UIView(frame: window.bounds)
        let field = UITextField(frame: CGRect(x: 0, y: 0, width: 100, height: 30))
        container.addSubview(field)
        window.addSubview(container)
        XCTAssertTrue(TextInputFinder.input(in: container) === field)
        XCTAssertNil(TextInputFinder.input(in: UILabel()))
    }
}
#endif
