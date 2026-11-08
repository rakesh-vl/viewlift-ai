#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

/// Turns live objects into JSON rows: Objective-C properties and ivars per class level,
/// Swift stored properties through `Mirror`, methods, and collection elements.
/// Child objects come back as registry handles so the dashboard can drill in.
@MainActor
struct ObjectValueReader {
    static let maxRowsPerLevel = 250
    static let maxMethods = 400
    static let maxCollectionItems = 100
    static let maxPreview = 300
    private static let summaryKeys: Set<String> = ["description", "debugDescription", "hash", "superclass"]

    let registry: ObjectHandleRegistering

    func explore(_ object: AnyObject) -> [String: JSONValue] {
        let cls: AnyClass = object_getClass(object) ?? type(of: object)
        let swiftRows = mirrorRows(object)
        let swiftLabels = Set(swiftRows.compactMap { row -> String? in
            guard case let .object(item) = row, case let .string(name) = item["name"] else { return nil }
            return name
        })
        var out: [String: JSONValue] = [
            "handle": .string(registry.register(object)),
            "class": .string(NSStringFromClass(cls)),
            "module": .string(class_getImageName(cls).map { String(cString: $0) } ?? ""),
            "description": .string(Self.preview(object, limit: 2_000)),
            "superclasses": .array(ObjCRuntimeList.chain(of: cls, limit: 32).dropFirst().map {
                .string(NSStringFromClass($0))
            }),
            "levels": .array(ObjCRuntimeList.chain(of: cls).map { level($0, object: object, skip: swiftLabels) }),
            "swift": .array(swiftRows),
            "methods": .array(methods(of: cls))
        ]
        if let collection = collection(object) {
            out["collection"] = .object(collection)
        }
        return out
    }

    /// Compact form used in tables: kind, preview, and a handle for objects.
    func encode(_ raw: Any?) -> JSONValue {
        guard let value = Self.unwrap(raw) else { return .object(["kind": .string("nil")]) }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return .object(["kind": .string("bool"), "value": .bool(number.boolValue)])
            }
            return Self.number(number.doubleValue)
        }
        if let text = value as? String {
            return .object(["kind": .string("string"), "value": .string(String(text.prefix(Self.maxPreview)))])
        }
        if type(of: value) is AnyClass {
            let object = value as AnyObject
            let cls: AnyClass = object_getClass(object) ?? type(of: object)
            return .object([
                "kind": .string(object is NSValue ? "value" : "object"),
                "class": .string(NSStringFromClass(cls)),
                "handle": .string(registry.register(object)),
                "preview": .string(Self.preview(object, limit: 160))
            ])
        }
        return .object([
            "kind": .string("struct"),
            "class": .string(String(describing: type(of: value))),
            "preview": .string(String(String(describing: value).prefix(Self.maxPreview)))
        ])
    }

    private func level(_ cls: AnyClass, object: AnyObject, skip: Set<String>) -> JSONValue {
        let properties = ObjCRuntimeList.properties(of: cls)
            .filter { !Self.summaryKeys.contains($0.name) }
            .sorted { $0.name < $1.name }
            .prefix(Self.maxRowsPerLevel)
            .map { propertyRow($0, object: object) }
        let ivars = ObjCRuntimeList.ivars(of: cls)
            .compactMap { ivarRow($0, object: object, skip: skip) }
            .prefix(Self.maxRowsPerLevel)
        return .object([
            "class": .string(NSStringFromClass(cls)),
            "properties": .array(Array(properties)),
            "ivars": .array(Array(ivars))
        ])
    }

    private func propertyRow(_ info: ObjCPropertyInfo, object: AnyObject) -> JSONValue {
        var row: [String: JSONValue] = [
            "name": .string(info.name),
            "type": .string(info.type.display),
            "encoding": .string(info.type.raw),
            "writable": .bool(!info.readonly && ObjectValueWriter.supports(info.type)),
            "source": .string("property")
        ]
        row["value"] = readProperty(info, object: object)
        return .object(row)
    }

    private func readProperty(_ info: ObjCPropertyInfo, object: AnyObject) -> JSONValue {
        guard let target = object as? NSObject else { return Self.unreadable("Not an NSObject") }
        // UIKit private getters (e.g. _UITraitOverrides) assert/trap; ObjCGuard cannot catch EXC_BREAKPOINT.
        if Self.shouldSkipPropertyRead(info, object: target) {
            return Self.unreadable("Skipped unsafe property")
        }
        let getter = Self.safeGetter(info.getter, object: target)
        guard target.responds(to: NSSelectorFromString(getter)) else { return Self.unreadable("No getter") }
        switch ObjCGuard.run({ target.value(forKey: getter) as Any? }) {
        case let .success(value):
            if info.type.isBoolLike, let number = value as? NSNumber {
                return .object(["kind": .string("bool"), "value": .bool(number.boolValue)])
            }
            return encode(value)
        case let .failure(error):
            return Self.unreadable(String(describing: error))
        }
    }

    /// Private UIKit/AppKit surfaces and trait-override KVC paths abort instead of raising NSException.
    static func shouldSkipPropertyRead(_ info: ObjCPropertyInfo, object: NSObject) -> Bool {
        if info.name.hasPrefix("_") || info.getter.hasPrefix("_") { return true }
        let className = NSStringFromClass(type(of: object))
        if className.hasPrefix("_") { return true }
        return Self.unsafePropertyNames.contains(info.name) || Self.unsafePropertyNames.contains(info.getter)
    }

    private static let unsafePropertyNames: Set<String> = [
        "traitOverrides"
    ]

    private func ivarRow(_ ivar: Ivar, object: AnyObject, skip: Set<String>) -> JSONValue? {
        guard let rawName = ivar_getName(ivar) else { return nil }
        let name = String(cString: rawName)
        let encoding = ivar_getTypeEncoding(ivar).map { String(cString: $0) } ?? ""
        let trimmed = name.hasPrefix("$__lazy_storage_$_") ? String(name.dropFirst(18)) : name
        if encoding.isEmpty || skip.contains(trimmed) { return nil }
        let type = ObjCType(encoding)
        return .object([
            "name": .string(name),
            "type": .string(type.display),
            "encoding": .string(type.raw),
            "writable": .bool(false),
            "source": .string("ivar"),
            "value": readIvar(ivar, type: type, object: object)
        ])
    }

    private func readIvar(_ ivar: Ivar, type: ObjCType, object: AnyObject) -> JSONValue {
        if type.isObject {
            return encode(object_getIvar(object, ivar))
        }
        guard type.isScalar else { return Self.unreadable(type.display) }
        let base = UnsafeRawPointer(Unmanaged.passUnretained(object).toOpaque())
        return Self.scalar(at: base.advanced(by: ivar_getOffset(ivar)), code: type.code)
    }

    private func mirrorRows(_ object: AnyObject) -> [JSONValue] {
        var rows: [JSONValue] = []
        var mirror: Mirror? = Mirror(reflecting: object)
        while let current = mirror, rows.count < Self.maxRowsPerLevel {
            for child in current.children {
                guard let label = child.label else { continue }
                let name = label.hasPrefix("$__lazy_storage_$_") ? String(label.dropFirst(18)) : label
                rows.append(.object([
                    "name": .string(name),
                    "type": .string(String(describing: Swift.type(of: child.value))),
                    "owner": .string(String(describing: current.subjectType)),
                    "writable": .bool(false),
                    "source": .string("swift"),
                    "value": encode(child.value)
                ]))
            }
            mirror = current.superclassMirror
        }
        return rows
    }

    private func methods(of cls: AnyClass) -> [JSONValue] {
        ObjCRuntimeList.methods(of: cls)
            .map { method -> (String, Method) in (NSStringFromSelector(method_getName(method)), method) }
            .sorted { $0.0 < $1.0 }
            .prefix(Self.maxMethods)
            .map { name, method in
                .object([
                    "selector": .string(name),
                    "returns": .string(ObjCRuntimeList.returnType(of: method).display),
                    "callable": .bool(ObjectMethodInvoker.isCallable(selector: name, method: method))
                ])
            }
    }

    private func collection(_ object: AnyObject) -> [String: JSONValue]? {
        let limit = Self.maxCollectionItems
        if let array = object as? NSArray {
            let items = (0..<min(array.count, limit)).map { index in
                JSONValue.object(["key": .string("\(index)"), "value": encode(array[index])])
            }
            return ["count": .number(Double(array.count)), "items": .array(items)]
        }
        if let dictionary = object as? NSDictionary {
            let items = dictionary.allKeys.prefix(limit).map { key in
                JSONValue.object([
                    "key": .string(String(String(describing: key).prefix(Self.maxPreview))),
                    "value": encode(dictionary[key])
                ])
            }
            return ["count": .number(Double(dictionary.count)), "items": .array(items)]
        }
        if let set = object as? NSSet {
            let items = set.allObjects.prefix(limit).enumerated().map { index, item in
                JSONValue.object(["key": .string("\(index)"), "value": encode(item)])
            }
            return ["count": .number(Double(set.count)), "items": .array(items)]
        }
        if let ordered = object as? NSOrderedSet {
            let items = (0..<min(ordered.count, limit)).map { index in
                JSONValue.object(["key": .string("\(index)"), "value": encode(ordered[index])])
            }
            return ["count": .number(Double(ordered.count)), "items": .array(items)]
        }
        return nil
    }

    /// Getters that load or create state are swapped for side-effect-free variants.
    private static func safeGetter(_ getter: String, object: NSObject) -> String {
        #if canImport(UIKit)
        if getter == "view", object is UIViewController {
            return "viewIfLoaded"
        }
        #endif
        return getter
    }

    static func preview(_ object: AnyObject, limit: Int) -> String {
        guard let target = object as? NSObject else {
            return String(String(describing: object).prefix(limit))
        }
        switch ObjCGuard.run({ target.description }) {
        case let .success(text): return String(text.prefix(limit))
        case .failure: return "<\(NSStringFromClass(type(of: target)))>"
        }
    }

    private static func unwrap(_ value: Any?) -> Any? {
        guard let value else { return nil }
        let mirror = Mirror(reflecting: value)
        guard mirror.displayStyle == .optional else { return value }
        guard let first = mirror.children.first else { return nil }
        return unwrap(first.value)
    }

    private static func unreadable(_ reason: String) -> JSONValue {
        .object(["kind": .string("unreadable"), "preview": .string(reason)])
    }
}

/// Raw scalar ivars and JSON-safe numbers.
extension ObjectValueReader {
    private static let scalarLoaders: [Character: (UnsafeRawPointer) -> Double] = [
        "C": { Double($0.load(as: UInt8.self)) },
        "s": { Double($0.load(as: Int16.self)) },
        "S": { Double($0.load(as: UInt16.self)) },
        "i": { Double($0.load(as: Int32.self)) },
        "I": { Double($0.load(as: UInt32.self)) },
        "l": { Double($0.load(as: Int64.self)) },
        "q": { Double($0.load(as: Int64.self)) },
        "L": { Double($0.load(as: UInt64.self)) },
        "Q": { Double($0.load(as: UInt64.self)) },
        "f": { Double($0.load(as: Float.self)) },
        "d": { $0.load(as: Double.self) }
    ]

    private static func scalar(at pointer: UnsafeRawPointer, code: Character) -> JSONValue {
        if code == "B" || code == "c" {
            let flag = code == "B" ? pointer.load(as: Bool.self) : pointer.load(as: Int8.self) != 0
            return .object(["kind": .string("bool"), "value": .bool(flag)])
        }
        guard let load = scalarLoaders[code] else { return unreadable(String(code)) }
        return number(load(pointer))
    }

    /// JSON has no NaN or infinity, and one non-finite value would fail the whole reply.
    static func number(_ value: Double) -> JSONValue {
        guard value.isFinite else {
            return .object(["kind": .string("number"), "preview": .string(String(value))])
        }
        return .object(["kind": .string("number"), "value": .number(value)])
    }
}
#endif
