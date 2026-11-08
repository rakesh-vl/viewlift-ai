import Foundation
@testable import ViewliftNetworkCapture
import XCTest

@objc(VLExplorerFixture)
final class ExplorerFixture: NSObject {
    @objc dynamic var title = "start"
    @objc dynamic var count = 3
    @objc dynamic var enabled = false
    @objc dynamic var child: NSObject?
    @objc let locked = "fixed"
    var swiftOnly = [1, 2, 3]

    @objc func greeting() -> String { "hello" }
    @objc func doubled() -> Int { count * 2 }
    @objc func touch() { title = "touched" }
    @objc func setUpSomething() {}
}

/// Mimics UIKit private class names (`_UITraitOverrides`) that trap on KVC property reads.
@objc(_VLPrivateExplorerStub)
final class PrivateExplorerStub: NSObject {}

final class ObjectHandleRegistryTests: XCTestCase {
    func testSameObjectKeepsHandleAndReleasedObjectDisappears() {
        let registry = ObjectHandleRegistry(capacity: 8)
        var object: NSObject? = NSObject()
        let first = registry.register(object!)
        XCTAssertEqual(registry.register(object!), first)
        XCTAssertTrue(registry.object(for: first) === object)
        object = nil
        XCTAssertNil(registry.object(for: first))
    }

    func testEvictsLeastRecentlyUsedPastCapacity() {
        let registry = ObjectHandleRegistry(capacity: 8)
        let objects = (0..<20).map { _ in NSObject() }
        let handles = objects.map(registry.register)
        XCTAssertLessThanOrEqual(registry.count, 8)
        XCTAssertNil(registry.object(for: handles[0]))
        XCTAssertTrue(registry.object(for: handles[19]) === objects[19])
    }
}

@MainActor
final class ObjectExplorerTests: XCTestCase {
    private var registry = ObjectHandleRegistry(capacity: 64)
    private lazy var service = ObjectExplorerService(registry: registry)

    func testTypeEncodingDisplay() {
        XCTAssertEqual(ObjCType("@\"NSString\"").display, "NSString *")
        XCTAssertEqual(ObjCType("@").display, "id")
        XCTAssertEqual(ObjCType("q").display, "long long")
        XCTAssertEqual(ObjCType("{CGRect={CGPoint=dd}{CGSize=dd}}").structName, "CGRect")
        XCTAssertTrue(ObjCType("@?").isBlock)
        XCTAssertEqual(ObjCType("r*").code, "*")
    }

    func testExploreListsPropertiesSwiftFieldsAndMethods() throws {
        let fixture = ExplorerFixture()
        let handle = registry.register(fixture)
        let result = service.explore(["handle": .string(handle)])
        XCTAssertEqual(result["class"], .string("VLExplorerFixture"))
        let properties = rows(result, level: "properties")
        XCTAssertEqual(value(properties["title"]), .string("start"))
        XCTAssertEqual(value(properties["count"]), .number(3))
        XCTAssertEqual(value(properties["enabled"]), .bool(false))
        XCTAssertEqual(properties["locked"]?["writable"], .bool(false))
        XCTAssertEqual(properties["title"]?["writable"], .bool(true))
        guard case let .array(swift) = result["swift"] else { return XCTFail("missing swift rows") }
        XCTAssertTrue(swift.contains { row in
            if case let .object(item) = row { return item["name"] == .string("swiftOnly") }
            return false
        })
        guard case let .array(methods) = result["methods"] else { return XCTFail("missing methods") }
        let callable = methods.compactMap { row -> String? in
            guard case let .object(item) = row, item["callable"] == .bool(true),
                  case let .string(name) = item["selector"] else { return nil }
            return name
        }
        XCTAssertTrue(callable.contains("greeting"))
        XCTAssertFalse(callable.contains("setUpSomething"))
        XCTAssertFalse(callable.contains("dealloc"))
    }

    func testSetValidatesTypesAndReadOnly() {
        let fixture = ExplorerFixture()
        let handle = JSONValue.string(registry.register(fixture))
        _ = service.set(["handle": handle, "key": .string("title"), "value": .string("changed")])
        XCTAssertEqual(fixture.title, "changed")
        _ = service.set(["handle": handle, "key": .string("count"), "value": .number(9)])
        XCTAssertEqual(fixture.count, 9)
        _ = service.set(["handle": handle, "key": .string("enabled"), "value": .bool(true)])
        XCTAssertTrue(fixture.enabled)
        XCTAssertNotNil(service.set(["handle": handle, "key": .string("title"), "value": .number(1)])["error"])
        XCTAssertNotNil(service.set(["handle": handle, "key": .string("locked"), "value": .string("x")])["error"])
        XCTAssertNotNil(service.set(["handle": handle, "key": .string("count"), "value": .null])["error"])
        XCTAssertNotNil(service.set(["handle": .string("missing"), "key": .string("title")])["error"])
    }

    func testCallRunsGettersAndRefusesBlockedSelectors() {
        let fixture = ExplorerFixture()
        let handle = JSONValue.string(registry.register(fixture))
        let greeting = service.call(["handle": handle, "selector": .string("greeting")])
        XCTAssertEqual(greeting["result"], .object(["kind": .string("string"), "value": .string("hello")]))
        let doubled = service.call(["handle": handle, "selector": .string("doubled")])
        XCTAssertEqual(doubled["result"], .object(["kind": .string("number"), "value": .number(6)]))
        _ = service.call(["handle": handle, "selector": .string("touch")])
        XCTAssertEqual(fixture.title, "touched")
        XCTAssertNotNil(service.call(["handle": handle, "selector": .string("setUpSomething")])["error"])
        XCTAssertNotNil(service.call(["handle": handle, "selector": .string("dealloc")])["error"])
        XCTAssertNotNil(service.call(["handle": handle, "selector": .string("setTitle:")])["error"])
    }

    func testExceptionFromKVCIsReported() {
        let fixture = ExplorerFixture()
        let result = ObjCGuard.run { fixture.value(forKey: "noSuchKey") as Any? }
        guard case let .failure(error) = result, case .exception = error else {
            return XCTFail("expected an exception")
        }
    }

    func testSkipsPrivateAndTraitOverridePropertyReads() throws {
        let fixture = ExplorerFixture()
        let properties = ObjCRuntimeList.properties(of: ExplorerFixture.self)
        let publicTitle = try XCTUnwrap(properties.first { $0.name == "title" })
        XCTAssertFalse(ObjectValueReader.shouldSkipPropertyRead(publicTitle, object: fixture))

        let privateName = ObjCPropertyInfo.makeForTest(name: "_traitCollection", getter: "_traitCollection", type: "@")
        XCTAssertTrue(ObjectValueReader.shouldSkipPropertyRead(privateName, object: fixture))

        let traitOverrides = ObjCPropertyInfo.makeForTest(name: "traitOverrides", getter: "traitOverrides", type: "@")
        XCTAssertTrue(ObjectValueReader.shouldSkipPropertyRead(traitOverrides, object: fixture))

        let privateClass = PrivateExplorerStub()
        XCTAssertTrue(ObjectValueReader.shouldSkipPropertyRead(publicTitle, object: privateClass))
    }

    func testInstancesFindsLiveObjects() {
        let kept = [ExplorerFixture(), ExplorerFixture()]
        let result = service.instances(["class": .string("VLExplorerFixture")])
        guard case let .array(rows) = result["instances"] else { return XCTFail("missing instances") }
        XCTAssertGreaterThanOrEqual(rows.count, kept.count)
        XCTAssertNotNil(service.instances(["class": .string("NoSuchClassAnywhere")])["error"])
    }

    func testRootsIncludeSharedObjects() {
        guard case let .array(rows) = service.roots()["roots"] else { return XCTFail("missing roots") }
        XCTAssertTrue(rows.contains { row in
            if case let .object(item) = row { return item["name"] == .string("Bundle.main") }
            return false
        })
    }

    private func rows(_ result: [String: JSONValue], level key: String) -> [String: [String: JSONValue]] {
        guard case let .array(levels) = result["levels"], case let .object(first) = levels.first,
              case let .array(items) = first[key] else {
            return [:]
        }
        var out: [String: [String: JSONValue]] = [:]
        for case let .object(item) in items {
            if case let .string(name) = item["name"] { out[name] = item }
        }
        return out
    }

    private func value(_ row: [String: JSONValue]?) -> JSONValue? {
        guard case let .object(value) = row?["value"] else { return nil }
        return value["value"]
    }
}

final class RuntimeBrowserTests: XCTestCase {
    func testLibrariesAndImageClasses() throws {
        guard case let .array(images) = RuntimeBrowser.libraries()["images"] else { return XCTFail("missing images") }
        XCTAssertFalse(images.isEmpty)
        let path = try XCTUnwrap(class_getImageName(ExplorerFixture.self).map { String(cString: $0) })
        guard case let .array(classes) = RuntimeBrowser.classes(inImage: path)["classes"] else {
            return XCTFail("missing classes")
        }
        XCTAssertTrue(classes.contains(.string("VLExplorerFixture")))
    }

    func testDescribeReportsTypedProperties() {
        let result = RuntimeBrowser.describe("VLExplorerFixture")
        XCTAssertEqual(result["superclass"], .string("NSObject"))
        guard case let .array(properties) = result["properties"] else { return XCTFail("missing properties") }
        XCTAssertTrue(properties.contains(.object([
            "name": .string("title"), "type": .string("NSString *"),
            "readonly": .bool(false), "weak": .bool(false)
        ])))
    }
}
