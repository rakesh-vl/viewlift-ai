#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// Property sheet for one view, plus the short labels the hierarchy tree shows on every node.
enum UIViewSheet {
    struct Marks {
        let short: String
        let module: String
        let role: String
        let controller: String
        let swiftui: String
        let childInsideHost: Bool
    }

    @MainActor
    static func marks(for view: UIView, ancestorIsHost: Bool) -> Marks {
        let described = String(describing: type(of: view))
        let qualified = String(reflecting: type(of: view))
        let split = UITypeName.moduleAndShort(qualified: qualified, described: described)
        let selfHost = UITypeName.isSwiftUIHost(described) || UITypeName.isSwiftUIHost(qualified)
        let owners = controllers(from: view)
        var controller = ""
        var controllerHost = false
        var swiftui = UITypeName.appViewName(in: qualified) ?? UITypeName.appViewName(in: described) ?? ""
        if let owner = owners.first {
            let ownerDescribed = String(describing: type(of: owner))
            let ownerQualified = String(reflecting: type(of: owner))
            controller = UITypeName.moduleAndShort(qualified: ownerQualified, described: ownerDescribed).short
            controllerHost = UITypeName.isSwiftUIHost(ownerDescribed) || UITypeName.isSwiftUIHost(ownerQualified)
            if swiftui.isEmpty {
                swiftui = UITypeName.appViewName(in: ownerQualified) ?? UITypeName.appViewName(in: ownerDescribed) ?? ""
            }
        }
        let role = UITypeName.role(
            selfIsHost: selfHost,
            ancestorIsHost: ancestorIsHost,
            controllerIsHost: controllerHost
        )
        return Marks(
            short: split.short,
            module: split.module,
            role: role,
            controller: controller,
            swiftui: role == "uikit" ? "" : swiftui,
            childInsideHost: selfHost || ancestorIsHost || controllerHost
        )
    }

    @MainActor
    static func make(for view: UIView) -> [String: JSONValue] {
        let info = marks(for: view, ancestorIsHost: ancestorHost(of: view))
        let sections = [identity(view, info)] + UIViewDetails.sections(for: view)
        let filled = sections.filter { !$0.rows.isEmpty }
        return [
            "found": .bool(true),
            "id": .string(String(ObjectIdentifier(view).hashValue)),
            "title": .string(info.short),
            "summary": .string(summary(info)),
            "role": .string(info.role),
            "sections": .array(filled.map(\.json))
        ]
    }

    private static func summary(_ info: Marks) -> String {
        switch info.role {
        case "swiftui":
            if info.swiftui.isEmpty {
                return "\(info.short) is the UIKit view SwiftUI created to draw this content."
            }
            return "SwiftUI view \(info.swiftui). \(info.short) is the UIKit view that draws it."
        case "uikit-in-swiftui":
            let host = info.swiftui.isEmpty ? "a SwiftUI screen" : "SwiftUI view \(info.swiftui)"
            return "\(info.short) is a UIKit view drawn inside \(host)."
        default:
            let owner = info.controller.isEmpty ? "" : " Shown by \(info.controller)."
            return "\(info.short) is a UIKit view.\(owner)"
        }
    }

    @MainActor
    private static func identity(_ view: UIView, _ info: Marks) -> ViewSheetSection {
        var rows = [("Class", info.short), ("Kind", kindLabel(info.role))]
        if !info.module.isEmpty { rows.append(("Module", info.module)) }
        let inherits = superclasses(of: view)
        if !inherits.isEmpty { rows.append(("Inherits", inherits)) }
        if !info.swiftui.isEmpty { rows.append(("SwiftUI view", info.swiftui)) }
        if !info.controller.isEmpty { rows.append(("Screen", info.controller)) }
        if view.tag != 0 { rows.append(("Tag", String(view.tag))) }
        if let name = view.restorationIdentifier, !name.isEmpty {
            rows.append(("Restoration id", ViewSheetFormat.clip(name)))
        }
        return ViewSheetSection(title: "What this is", rows: rows)
    }

    private static func superclasses(of view: UIView) -> String {
        var names: [String] = []
        var current: AnyClass? = type(of: view).superclass()
        while let type = current, names.count < 4 {
            let name = String(describing: type)
            names.append(name)
            if name == "UIView" || name == "UIResponder" { break }
            current = type.superclass()
        }
        return names.joined(separator: " → ")
    }

    private static func ancestorHost(of view: UIView) -> Bool {
        var current = view.superview
        while let view = current {
            let described = String(describing: type(of: view))
            let qualified = String(reflecting: type(of: view))
            if UITypeName.isSwiftUIHost(described) || UITypeName.isSwiftUIHost(qualified) { return true }
            current = view.superview
        }
        return false
    }

    static func controllers(from view: UIView) -> [UIViewController] {
        var found: [UIViewController] = []
        var responder: UIResponder? = view.next
        while let current = responder, found.count < 5 {
            if let controller = current as? UIViewController { found.append(controller) }
            responder = current.next
        }
        return found
    }

    private static func kindLabel(_ role: String) -> String {
        switch role {
        case "swiftui": return "SwiftUI"
        case "uikit-in-swiftui": return "UIKit inside SwiftUI"
        default: return "UIKit"
        }
    }

}
#endif
#endif
