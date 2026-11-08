#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime

#if canImport(UIKit)
import UIKit
#endif

/// Type-checked KVC writes. Only settable Objective-C properties whose type maps cleanly
/// from JSON are accepted, and KVC runs inside the exception guard.
@MainActor
enum ObjectValueWriter {
    private static let structNames: Set<String> = ["CGRect", "CGPoint", "CGSize", "UIEdgeInsets"]

    static func supports(_ type: ObjCType) -> Bool {
        if type.isScalar || type.isObject { return true }
        #if canImport(UIKit)
        return type.structName.map(structNames.contains) ?? false
        #else
        return false
        #endif
    }

    static func set(_ object: AnyObject, key: String, value: JSONValue) -> Result<Void, ObjectExplorerError> {
        guard let target = object as? NSObject,
              let cls = object_getClass(object),
              let property = class_getProperty(cls, key),
              let info = ObjCPropertyInfo(property) else {
            return .failure(.notFound)
        }
        guard !info.readonly, let setter = info.setter,
              target.responds(to: NSSelectorFromString(setter)) else {
            return .failure(.readOnly(key))
        }
        let converted: Any?
        switch convert(value, to: info.type) {
        case let .success(item): converted = item
        case let .failure(error): return .failure(error)
        }
        if converted == nil, !info.type.isObject {
            return .failure(.invalidValue("\(key) cannot be nil"))
        }
        return ObjCGuard.run { target.setValue(converted, forKey: info.name) }
    }

    static func convert(_ value: JSONValue, to type: ObjCType) -> Result<Any?, ObjectExplorerError> {
        if case .null = value { return .success(nil) }
        if type.isScalar { return scalar(value, type: type) }
        if type.isObject { return object(value, className: type.className) }
        #if canImport(UIKit)
        if let name = type.structName, structNames.contains(name), case let .string(text) = value {
            return structValue(text, name: name)
        }
        #endif
        return .failure(.unsupportedType(type.display))
    }

    private static func scalar(_ value: JSONValue, type: ObjCType) -> Result<Any?, ObjectExplorerError> {
        switch value {
        case let .bool(flag):
            return .success(NSNumber(value: flag))
        case let .number(number):
            return .success(type.isBoolLike ? NSNumber(value: number != 0) : NSNumber(value: number))
        case let .string(text):
            if let number = Double(text.trimmingCharacters(in: .whitespaces)) {
                return .success(NSNumber(value: number))
            }
            if type.isBoolLike, let flag = Bool(text.lowercased()) {
                return .success(NSNumber(value: flag))
            }
            return .failure(.invalidValue("Expected a number"))
        default:
            return .failure(.invalidValue("Expected a number"))
        }
    }

    private static func object(_ value: JSONValue, className: String?) -> Result<Any?, ObjectExplorerError> {
        switch className {
        case "NSString", "NSMutableString":
            guard case let .string(text) = value else { return .failure(.invalidValue("Expected a string")) }
            return .success(className == "NSMutableString" ? NSMutableString(string: text) : text)
        case "NSNumber":
            guard let number = number(value) else { return .failure(.invalidValue("Expected a number")) }
            return .success(number)
        case "NSURL", "NSDate", "UIColor":
            return typedObject(value, className: className ?? "")
        case nil, "NSObject":
            return .success(foundation(value))
        case "NSArray", "NSDictionary":
            let matches = className == "NSArray" ? value.isArray : value.isObject
            return matches ? .success(foundation(value)) : .failure(.invalidValue("Expected JSON \(className ?? "")"))
        default:
            return .failure(.unsupportedType(className ?? "id"))
        }
    }

    private static func number(_ value: JSONValue) -> NSNumber? {
        switch value {
        case let .number(number): return NSNumber(value: number)
        case let .bool(flag): return NSNumber(value: flag)
        default: return nil
        }
    }

    private static func typedObject(_ value: JSONValue, className: String) -> Result<Any?, ObjectExplorerError> {
        switch (className, value) {
        case ("NSURL", .string(let text)):
            guard let url = URL(string: text) else { return .failure(.invalidValue("Invalid URL")) }
            return .success(url as NSURL)
        case ("NSDate", .number(let seconds)):
            return .success(Date(timeIntervalSince1970: seconds) as NSDate)
        #if canImport(UIKit)
        case ("UIColor", .string(let text)):
            guard let color = color(hex: text) else { return .failure(.invalidValue("Expected #RRGGBB or #RRGGBBAA")) }
            return .success(color)
        #endif
        default:
            return .failure(.invalidValue("Unexpected value for \(className)"))
        }
    }

    static func foundation(_ value: JSONValue) -> Any {
        switch value {
        case let .string(item): return item
        case let .number(item): return NSNumber(value: item)
        case let .bool(item): return NSNumber(value: item)
        case let .array(items): return items.map(foundation)
        case let .object(items): return items.mapValues(foundation)
        case .null: return NSNull()
        }
    }

    #if canImport(UIKit)
    private static func structValue(_ text: String, name: String) -> Result<Any?, ObjectExplorerError> {
        switch name {
        case "CGRect": return .success(NSValue(cgRect: NSCoder.cgRect(for: text)))
        case "CGPoint": return .success(NSValue(cgPoint: NSCoder.cgPoint(for: text)))
        case "CGSize": return .success(NSValue(cgSize: NSCoder.cgSize(for: text)))
        case "UIEdgeInsets": return .success(NSValue(uiEdgeInsets: NSCoder.uiEdgeInsets(for: text)))
        default: return .failure(.unsupportedType(name))
        }
    }

    static func color(hex: String) -> UIColor? {
        var text = hex.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6 || text.count == 8, let raw = UInt64(text, radix: 16) else { return nil }
        let rgba = text.count == 6 ? (raw << 8) | 0xFF : raw
        return UIColor(
            red: CGFloat((rgba >> 24) & 0xFF) / 255,
            green: CGFloat((rgba >> 16) & 0xFF) / 255,
            blue: CGFloat((rgba >> 8) & 0xFF) / 255,
            alpha: CGFloat(rgba & 0xFF) / 255
        )
    }
    #endif
}

/// Calls zero-argument instance methods. Selectors that change ownership or tear objects
/// down are refused, and so is anything with arguments.
@MainActor
enum ObjectMethodInvoker {
    private static let blockedNames: Set<String> = [
        "dealloc", "release", "retain", "autorelease", "retainCount", "finalize",
        "removeFromSuperview", "removeFromParent", "dismiss", "abort", "exit"
    ]
    private static let blockedPrefixes = ["init", "copy", "mutableCopy", "new", "set", "_set", ".cxx"]

    static func isCallable(selector: String, method: Method) -> Bool {
        guard !selector.contains(":"), method_getNumberOfArguments(method) == 2 else { return false }
        guard !blockedNames.contains(selector),
              !blockedPrefixes.contains(where: { selector.hasPrefix($0) }) else {
            return false
        }
        let type = ObjCRuntimeList.returnType(of: method)
        return type.code == "v" || type.isObject || type.isScalar || type.structName != nil
    }

    static func call(
        _ object: AnyObject,
        selector name: String,
        reader: ObjectValueReader
    ) -> Result<JSONValue, ObjectExplorerError> {
        let selector = NSSelectorFromString(name)
        guard let cls = object_getClass(object), let method = class_getInstanceMethod(cls, selector) else {
            return .failure(.notFound)
        }
        guard isCallable(selector: name, method: method) else { return .failure(.blockedSelector(name)) }
        let type = ObjCRuntimeList.returnType(of: method)
        let imp = method_getImplementation(method)
        if type.code == "v" {
            typealias VoidCall = @convention(c) (AnyObject, Selector) -> Void
            let call = unsafeBitCast(imp, to: VoidCall.self)
            return ObjCGuard.run { call(object, selector) }.map { .object(["kind": .string("void")]) }
        }
        if type.isObject {
            typealias ObjectCall = @convention(c) (AnyObject, Selector) -> Unmanaged<AnyObject>?
            let call = unsafeBitCast(imp, to: ObjectCall.self)
            return ObjCGuard.run { call(object, selector)?.takeUnretainedValue() }.map { reader.encode($0) }
        }
        guard let target = object as? NSObject else { return .failure(.unsupportedType(type.display)) }
        // KVC boxes scalar and struct returns into NSNumber and NSValue.
        return ObjCGuard.run { target.value(forKey: name) as Any? }.map { value in
            if type.isBoolLike, let number = value as? NSNumber {
                return .object(["kind": .string("bool"), "value": .bool(number.boolValue)])
            }
            return reader.encode(value)
        }
    }
}
#endif
