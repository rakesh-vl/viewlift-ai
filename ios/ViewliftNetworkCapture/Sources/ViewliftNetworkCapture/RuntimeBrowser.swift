#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

enum RuntimeBrowser {
    static func search(_ query: String) -> [String: JSONValue] {
        let needle = query.lowercased()
        let expected = objc_getClassList(nil, 0)
        guard expected > 0 else {
            return ["classes": .array([])]
        }
        let classes = UnsafeMutablePointer<AnyClass>.allocate(capacity: Int(expected))
        defer { classes.deallocate() }
        let count = objc_getClassList(
            AutoreleasingUnsafeMutablePointer<AnyClass>(classes),
            expected
        )
        var rows: [JSONValue] = []
        for index in 0..<Int(max(0, count)) {
            let cls: AnyClass = classes[index]
            let name = NSStringFromClass(cls)
            if needle.isEmpty || name.lowercased().contains(needle) {
                rows.append(.string(name))
                if rows.count >= 1_000 {
                    break
                }
            }
        }
        return ["classes": .array(rows), "truncated": .bool(rows.count >= 1_000)]
    }

    static func describe(_ className: String) -> [String: JSONValue] {
        guard let cls = NSClassFromString(className) else {
            return ["error": .string("Class not found")]
        }
        var methodCount: UInt32 = 0
        let methodList = class_copyMethodList(cls, &methodCount)
        defer { free(methodList) }
        var methods: [JSONValue] = []
        if let methodList {
            for index in 0..<Int(methodCount) {
                methods.append(.string(NSStringFromSelector(method_getName(methodList[index]))))
            }
        }
        var propertyCount: UInt32 = 0
        let propertyList = class_copyPropertyList(cls, &propertyCount)
        defer { free(propertyList) }
        var properties: [JSONValue] = []
        if let propertyList {
            for index in 0..<Int(propertyCount) {
                properties.append(.string(String(cString: property_getName(propertyList[index]))))
            }
        }
        var ivarCount: UInt32 = 0
        let ivarList = class_copyIvarList(cls, &ivarCount)
        defer { free(ivarList) }
        var ivars: [JSONValue] = []
        if let ivarList {
            for index in 0..<Int(ivarCount) {
                guard let name = ivar_getName(ivarList[index]) else { continue }
                ivars.append(.string(String(cString: name)))
            }
        }
        return [
            "class": .string(className),
            "superclass": .string(class_getSuperclass(cls).map(NSStringFromClass) ?? ""),
            "methods": .array(methods.sorted(by: Self.less)),
            "properties": .array(properties.sorted(by: Self.less)),
            "ivars": .array(ivars.sorted(by: Self.less))
        ]
    }

    private static func less(_ lhs: JSONValue, _ rhs: JSONValue) -> Bool {
        guard case let .string(left) = lhs, case let .string(right) = rhs else {
            return false
        }
        return left < right
    }
}
#endif
