#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime
import ViewliftCaptureObjC

enum ObjectExplorerError: Error, Equatable {
    case notFound
    case unsupportedType(String)
    case readOnly(String)
    case blockedSelector(String)
    case invalidValue(String)
    case exception(String)
}

extension JSONValue {
    var isArray: Bool {
        if case .array = self { return true }
        return false
    }

    var isObject: Bool {
        if case .object = self { return true }
        return false
    }
}

enum ObjCGuard {
    /// Runs `body`, turning a raised NSException into `.exception` instead of a crash.
    static func run<T>(_ body: () -> T) -> Result<T, ObjectExplorerError> {
        var output: T?
        let raised = VLCCatchException {
            output = body()
        }
        if let raised {
            return .failure(.exception("\(raised.name.rawValue): \(raised.reason ?? "")"))
        }
        guard let output else { return .failure(.exception("No value")) }
        return .success(output)
    }
}

/// One Objective-C type encoding, for example `@"NSString"`, `q`, or `{CGRect={CGPoint=dd}{CGSize=dd}}`.
struct ObjCType: Equatable {
    private static let qualifiers: Set<Character> = ["r", "n", "N", "o", "O", "R", "V", "A"]
    private static let names: [Character: String] = [
        "c": "char", "C": "unsigned char", "s": "short", "S": "unsigned short",
        "i": "int", "I": "unsigned int", "l": "long", "L": "unsigned long",
        "q": "long long", "Q": "unsigned long long", "f": "float", "d": "double",
        "B": "BOOL", "v": "void", "*": "char *", "#": "Class", ":": "SEL", "?": "unknown"
    ]
    static let scalarCodes: Set<Character> = ["c", "C", "s", "S", "i", "I", "l", "L", "q", "Q", "f", "d", "B"]

    let raw: String

    init(_ raw: String) {
        self.raw = String(raw.drop { Self.qualifiers.contains($0) })
    }

    var code: Character { raw.first ?? "?" }
    var isBlock: Bool { raw.hasPrefix("@?") }
    var isObject: Bool { code == "@" && !isBlock }
    var isScalar: Bool { raw.count == 1 && Self.scalarCodes.contains(code) }
    var isBoolLike: Bool { raw == "B" || raw == "c" }

    /// `NSString` for `@"NSString"`; nil for `id` and protocol-only types.
    var className: String? {
        guard isObject, raw.count > 3, raw.hasPrefix("@\""), raw.hasSuffix("\"") else { return nil }
        let inner = raw.dropFirst(2).dropLast()
        guard !inner.hasPrefix("<") else { return nil }
        return String(inner.prefix { $0 != "<" })
    }

    var structName: String? {
        guard code == "{" else { return nil }
        return String(raw.dropFirst().prefix { $0 != "=" && $0 != "}" })
    }

    var display: String {
        if isBlock { return "block" }
        if isObject { return className.map { "\($0) *" } ?? "id" }
        if let structName { return structName }
        if code == "^" { return "pointer" }
        return Self.names[code] ?? raw
    }
}

struct ObjCPropertyInfo {
    let name: String
    let type: ObjCType
    let readonly: Bool
    let weak: Bool
    let getter: String
    let setter: String?

    init?(_ property: objc_property_t) {
        name = String(cString: property_getName(property))
        var count: UInt32 = 0
        guard let list = property_copyAttributeList(property, &count) else { return nil }
        defer { free(list) }
        var attributes: [String: String] = [:]
        for index in 0..<Int(count) {
            let item = list[index]
            attributes[String(cString: item.name)] = String(cString: item.value)
        }
        type = ObjCType(attributes["T"] ?? "?")
        readonly = attributes["R"] != nil
        weak = attributes["W"] != nil
        getter = attributes["G"] ?? name
        if readonly {
            setter = nil
        } else {
            setter = attributes["S"] ?? "set\(name.prefix(1).uppercased())\(name.dropFirst()):"
        }
    }

    /// Test-only constructor for skip-list coverage without a live `objc_property_t`.
    static func makeForTest(name: String, getter: String, type: String) -> ObjCPropertyInfo {
        ObjCPropertyInfo(
            name: name,
            type: ObjCType(type),
            readonly: true,
            weak: false,
            getter: getter,
            setter: nil
        )
    }

    private init(
        name: String,
        type: ObjCType,
        readonly: Bool,
        weak: Bool,
        getter: String,
        setter: String?
    ) {
        self.name = name
        self.type = type
        self.readonly = readonly
        self.weak = weak
        self.getter = getter
        self.setter = setter
    }
}

enum ObjCRuntimeList {
    static func properties(of cls: AnyClass) -> [ObjCPropertyInfo] {
        var count: UInt32 = 0
        guard let list = class_copyPropertyList(cls, &count) else { return [] }
        defer { free(list) }
        return (0..<Int(count)).compactMap { ObjCPropertyInfo(list[$0]) }
    }

    static func ivars(of cls: AnyClass) -> [Ivar] {
        var count: UInt32 = 0
        guard let list = class_copyIvarList(cls, &count) else { return [] }
        defer { free(list) }
        return (0..<Int(count)).map { list[$0] }
    }

    static func methods(of cls: AnyClass) -> [Method] {
        var count: UInt32 = 0
        guard let list = class_copyMethodList(cls, &count) else { return [] }
        defer { free(list) }
        return (0..<Int(count)).map { list[$0] }
    }

    static func returnType(of method: Method) -> ObjCType {
        let raw = method_copyReturnType(method)
        defer { free(raw) }
        return ObjCType(String(cString: raw))
    }

    /// Superclass chain from `cls` upward, stopping before `NSObject` unless `cls` is `NSObject`.
    static func chain(of cls: AnyClass, limit: Int = 16) -> [AnyClass] {
        var out: [AnyClass] = []
        var current: AnyClass? = cls
        while let item = current, out.count < limit {
            if item == NSObject.self, !out.isEmpty { break }
            out.append(item)
            current = class_getSuperclass(item)
        }
        return out
    }
}
#endif
