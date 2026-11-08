@testable import ViewliftNetworkCapture
import XCTest

final class SwiftUIDebugTreeTests: XCTestCase {
    private func attribute(_ id: Int, _ body: [String: Any]) -> [String: Any] {
        ["id": id, "attribute": body]
    }

    private func node(
        type: String,
        origin: [Double],
        size: [Double],
        children: [[String: Any]] = []
    ) -> [String: Any] {
        [
            "properties": [
                attribute(0, ["type": type]),
                attribute(3, ["type": "CGPoint", "value": origin]),
                attribute(4, ["type": "CGSize", "value": size])
            ],
            "children": children
        ]
    }

    private func encode(_ object: Any) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }

    func testParseReadsTypeFrameAndChildren() throws {
        let spacer = node(type: "SwiftUI.Spacer", origin: [0, 10], size: [100, 20])
        let stack = node(
            type: "SwiftUI.VStack<SwiftUI.TupleView<(SwiftUI.Spacer, SwiftUI.Text)>>",
            origin: [0, 0],
            size: [100, 50],
            children: [spacer]
        )
        let nodes = SwiftUIDebugTree.parse(try encode([stack]))
        XCTAssertEqual(nodes.count, 1)
        XCTAssertEqual(nodes[0].name, "Vertical Stack")
        XCTAssertEqual(nodes[0].frame, CGRect(x: 0, y: 0, width: 100, height: 50))
        XCTAssertEqual(nodes[0].children.map(\.name), ["Spacer"])
        XCTAssertEqual(nodes[0].children[0].frame, CGRect(x: 0, y: 10, width: 100, height: 20))
    }

    func testParseReadsSubattributePoints() throws {
        let raw: [String: Any] = [
            "properties": [
                attribute(0, ["type": "SwiftUI.ZStack<SwiftUI.EmptyView>"]),
                attribute(3, ["subattributes": [["name": "x", "value": 4], ["name": "y", "value": 8]]]),
                attribute(4, ["subattributes": [["name": "width", "value": "30"], ["name": "height", "value": 40]]])
            ],
            "children": []
        ]
        let nodes = SwiftUIDebugTree.parse(try encode([raw]))
        XCTAssertEqual(nodes.first?.frame, CGRect(x: 4, y: 8, width: 30, height: 40))
        XCTAssertEqual(nodes.first?.name, "ZStack")
    }

    func testTransparentWrappersCollapseIntoChildren() throws {
        let text = node(type: "SwiftUI.Text", origin: [0, 0], size: [40, 12])
        let image = node(type: "SwiftUI.Image", origin: [50, 0], size: [12, 12])
        let tuple = node(
            type: "SwiftUI.TupleView<(SwiftUI.Text, SwiftUI.Image)>",
            origin: [0, 0],
            size: [62, 12],
            children: [text, image]
        )
        let modifier = "SwiftUI._EnvironmentKeyWritingModifier<Swift.Bool>"
        let environment = node(
            type: "SwiftUI.ModifiedContent<SwiftUI.TupleView<(SwiftUI.Text)>, \(modifier)>",
            origin: [0, 0],
            size: [62, 12],
            children: [tuple]
        )
        let nodes = SwiftUIDebugTree.parse(try encode([environment]))
        XCTAssertEqual(nodes.map(\.name), ["Text", "Image"])
    }

    func testModifierNamesReadLikeXcode() {
        let background = "SwiftUI.ModifiedContent<App.Card, SwiftUI._BackgroundModifier<SwiftUI.Color>>"
        XCTAssertEqual(SwiftUIDebugTree.displayName(for: background), "Background")
        XCTAssertFalse(SwiftUIDebugTree.isTransparent(background))
        let padding = "SwiftUI.ModifiedContent<SwiftUI.Text, SwiftUI._PaddingLayout>"
        XCTAssertEqual(SwiftUIDebugTree.displayName(for: padding), "Padding")
        XCTAssertEqual(
            SwiftUIDebugTree.displayName(for: "SwiftUI.GeometryReader<SwiftUI.Color>"),
            "GeometryReader<Color>"
        )
        XCTAssertEqual(SwiftUIDebugTree.displayName(for: "SwiftUI.HStack<SwiftUI.EmptyView>"), "Horizontal Stack")
    }

    func testButtonChainKeepsVisibleModifiers() throws {
        let inner = "_ViewModifier_Content<AccessibilityButtonBorderModifier>"
        let content = node(type: inner, origin: [0, 0], size: [20, 10])
        let padding = node(
            type: "ModifiedContent<\(inner), _PaddingLayout>",
            origin: [0, 0],
            size: [20, 10],
            children: [content]
        )
        let staticIf = node(
            type: "ModifiedContent<HStack<ButtonStyleConfiguration.Label>, StaticIf<A, B, C>>",
            origin: [0, 0],
            size: [20, 10],
            children: [padding]
        )
        let stack = node(
            type: "VStack<ResolvedButtonStyleBody<BorderlessButtonStyleBase>>",
            origin: [0, 0],
            size: [20, 10],
            children: [staticIf]
        )
        let button = node(
            type: "KeyboardShortcutBindingBehavior<PrimitiveButtonStyleConfiguration.Label>",
            origin: [0, 0],
            size: [20, 10],
            children: [stack]
        )
        let nodes = SwiftUIDebugTree.parse(try encode([button]))
        XCTAssertEqual(nodes.map(\.name), ["Button"])
        XCTAssertEqual(nodes.first?.children.map(\.name), ["Vertical Stack"])
        XCTAssertEqual(nodes.first?.children.first?.children.map(\.name), ["Padding"])
    }

    func testParseRejectsGarbage() {
        XCTAssertTrue(SwiftUIDebugTree.parse(Data("not json".utf8)).isEmpty)
        XCTAssertTrue(SwiftUIDebugTree.parse(Data("{\"x\":1}".utf8)).isEmpty)
    }
}
