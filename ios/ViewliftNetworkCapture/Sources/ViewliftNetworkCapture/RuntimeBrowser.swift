#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import MachO
import ObjectiveC.runtime

enum RuntimeBrowser {
    static let maxClasses = 1_000
    static let maxImageClasses = 3_000

    /// Classes whose name contains `query`. `appOnly` keeps classes loaded from the app bundle;
    /// `app_classes` always lists the matches that come from the app, so the dashboard can rank them.
    static func search(_ query: String, appOnly: Bool = false) -> [String: JSONValue] {
        let needle = query.lowercased()
        var count: UInt32 = 0
        guard let list = objc_copyClassList(&count) else {
            return ["classes": .array([])]
        }
        let raw = UnsafeMutableRawPointer(list)
        defer { free(raw) }
        // Reading through the autoreleasing pointer retains each class, and some root classes abort on retain.
        let pointers = raw.assumingMemoryBound(to: UnsafeRawPointer.self)
        let bundle = Bundle.main.bundlePath
        var rows: [String] = []
        var app: [String] = []
        for index in 0..<Int(count) {
            let cls: AnyClass = unsafeBitCast(pointers[index], to: AnyClass.self)
            let name = String(cString: class_getName(cls))
            guard needle.isEmpty || name.lowercased().contains(needle) else { continue }
            let image = class_getImageName(cls).map { String(cString: $0) } ?? ""
            let fromApp = !bundle.isEmpty && image.hasPrefix(bundle)
            if appOnly, !fromApp { continue }
            rows.append(name)
            if fromApp { app.append(name) }
            if rows.count >= maxClasses { break }
        }
        return [
            "classes": .array(rows.sorted().map(JSONValue.string)),
            "app_classes": .array(app.sorted().map(JSONValue.string)),
            "truncated": .bool(rows.count >= maxClasses)
        ]
    }

    private struct ImageRow {
        let path: String
        let app: Bool
        let classes: Int
    }

    /// Every dyld image with its Objective-C class count; app images first.
    static func libraries() -> [String: JSONValue] {
        let bundle = Bundle.main.bundlePath
        var rows: [ImageRow] = []
        for index in 0..<_dyld_image_count() {
            guard let name = _dyld_get_image_name(index) else { continue }
            let path = String(cString: name)
            var count: UInt32 = 0
            if let names = objc_copyClassNamesForImage(name, &count) {
                free(UnsafeMutableRawPointer(mutating: names))
            }
            rows.append(ImageRow(path: path, app: !bundle.isEmpty && path.hasPrefix(bundle), classes: Int(count)))
        }
        rows.sort { lhs, rhs in
            lhs.app != rhs.app ? lhs.app : lhs.path < rhs.path
        }
        return [
            "count": .number(Double(rows.count)),
            "images": .array(rows.map { row in
                .object([
                    "path": .string(row.path),
                    "name": .string((row.path as NSString).lastPathComponent),
                    "app": .bool(row.app),
                    "classes": .number(Double(row.classes))
                ])
            })
        ]
    }

    static func classes(inImage path: String) -> [String: JSONValue] {
        var count: UInt32 = 0
        guard !path.isEmpty, let names = path.withCString({ objc_copyClassNamesForImage($0, &count) }) else {
            return ["image": .string(path), "classes": .array([])]
        }
        defer { free(UnsafeMutableRawPointer(mutating: names)) }
        let rows = (0..<Int(count)).map { String(cString: names[$0]) }.sorted()
        return [
            "image": .string(path),
            "classes": .array(rows.prefix(maxImageClasses).map(JSONValue.string)),
            "truncated": .bool(rows.count > maxImageClasses)
        ]
    }

    static func describe(_ className: String) -> [String: JSONValue] {
        guard let cls = NSClassFromString(className) else {
            return ["error": .string("Class not found")]
        }
        let properties = ObjCRuntimeList.properties(of: cls).sorted { $0.name < $1.name }
        let ivars = ObjCRuntimeList.ivars(of: cls).compactMap(ivarRow)
        var out: [String: JSONValue] = [
            "class": .string(className),
            "superclass": .string(class_getSuperclass(cls).map(NSStringFromClass) ?? ""),
            "superclasses": .array(ObjCRuntimeList.chain(of: cls, limit: 32).dropFirst().map {
                .string(NSStringFromClass($0))
            }),
            "image": .string(class_getImageName(cls).map { String(cString: $0) } ?? ""),
            "instance_size": .number(Double(class_getInstanceSize(cls))),
            "protocols": .array(protocols(of: cls)),
            "methods": .array(methodRows(cls)),
            "properties": .array(properties.map { info in
                .object([
                    "name": .string(info.name),
                    "type": .string(info.type.display),
                    "readonly": .bool(info.readonly),
                    "weak": .bool(info.weak)
                ])
            }),
            "ivars": .array(ivars)
        ]
        if let meta = object_getClass(cls) {
            out["class_methods"] = .array(methodRows(meta))
        }
        return out
    }

    private static func methodRows(_ cls: AnyClass) -> [JSONValue] {
        ObjCRuntimeList.methods(of: cls)
            .map { method in
                (NSStringFromSelector(method_getName(method)), ObjCRuntimeList.returnType(of: method).display)
            }
            .sorted { $0.0 < $1.0 }
            .map { name, returns in .object(["selector": .string(name), "returns": .string(returns)]) }
    }

    private static func ivarRow(_ ivar: Ivar) -> JSONValue? {
        guard let name = ivar_getName(ivar) else { return nil }
        let encoding = ivar_getTypeEncoding(ivar).map { String(cString: $0) } ?? ""
        return .object([
            "name": .string(String(cString: name)),
            "type": .string(encoding.isEmpty ? "Swift" : ObjCType(encoding).display),
            "offset": .number(Double(ivar_getOffset(ivar)))
        ])
    }

    private static func protocols(of cls: AnyClass) -> [JSONValue] {
        var count: UInt32 = 0
        guard let list = class_copyProtocolList(cls, &count) else { return [] }
        defer { free(UnsafeMutableRawPointer(list)) }
        return (0..<Int(count))
            .map { String(cString: protocol_getName(list[$0])) }
            .sorted()
            .map(JSONValue.string)
    }
}
#endif
