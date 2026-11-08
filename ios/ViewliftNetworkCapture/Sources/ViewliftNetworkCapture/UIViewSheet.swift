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
        let typeName: String
        let childInsideHost: Bool
    }

    @MainActor
    static func marks(for view: UIView, ancestorIsHost: Bool, ancestorSwiftUI: String = "") -> Marks {
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
        if swiftui.isEmpty, role != "uikit" {
            swiftui = ancestorSwiftUI
        }
        let full = qualified.contains("<") ? qualified : described
        return Marks(
            short: split.short,
            module: split.module,
            role: role,
            controller: controller,
            swiftui: role == "uikit" ? "" : swiftui,
            typeName: String(full.prefix(160)),
            childInsideHost: selfHost || ancestorIsHost || controllerHost
        )
    }

    @MainActor
    static func make(for view: UIView) -> [String: JSONValue] {
        let hosted = ancestorHost(of: view)
        let info = marks(for: view, ancestorIsHost: hosted, ancestorSwiftUI: ancestorSwiftUI(of: view))
        let sections = [identity(view, info)] + UIViewDetails.sections(for: view)
        let filled = sections.filter { !$0.rows.isEmpty }
        let title = UITypeName.displayName(uikit: info.short, swiftui: info.swiftui, role: info.role)
        return [
            "found": .bool(true),
            "id": .string(String(ObjectIdentifier(view).hashValue)),
            "title": .string(title),
            "uikit": .string(info.short),
            "swiftui": .string(info.swiftui),
            "type": .string(info.typeName),
            "summary": .string(summary(info)),
            "role": .string(info.role),
            "sections": .array(filled.map(\.json)),
            "explorer": .object(UILayoutExplorer.make(for: view))
        ]
    }

    private static func summary(_ info: Marks) -> String {
        switch info.role {
        case "swiftui":
            if info.swiftui.isEmpty {
                return "\(info.short) hosts SwiftUI. The SwiftUI view name is not on this UIKit type."
            }
            return "SwiftUI \(info.swiftui), drawn by UIKit \(info.short)."
        case "uikit-in-swiftui":
            let host = info.swiftui.isEmpty ? "a SwiftUI view" : "SwiftUI \(info.swiftui)"
            return "UIKit \(info.short) inside \(host)."
        default:
            let owner = info.controller.isEmpty ? "" : " Shown by \(info.controller)."
            return "UIKit \(info.short).\(owner)"
        }
    }

    @MainActor
    private static func identity(_ view: UIView, _ info: Marks) -> ViewSheetSection {
        var rows = [("Kind", kindLabel(info.role)), ("UIKit class", info.short)]
        if !info.swiftui.isEmpty { rows.append(("SwiftUI view", info.swiftui)) }
        if info.typeName != info.short { rows.append(("Full type", info.typeName)) }
        if !info.module.isEmpty { rows.append(("Module", info.module)) }
        let inherits = superclasses(of: view)
        if !inherits.isEmpty { rows.append(("Inherits", inherits)) }
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

    @MainActor
    private static func ancestorSwiftUI(of view: UIView) -> String {
        var current = view.superview
        while let ancestor = current {
            let described = String(describing: type(of: ancestor))
            let qualified = String(reflecting: type(of: ancestor))
            let host = UITypeName.isSwiftUIHost(described) || UITypeName.isSwiftUIHost(qualified)
            if host {
                return UITypeName.appViewName(in: qualified)
                    ?? UITypeName.appViewName(in: described)
                    ?? ""
            }
            current = ancestor.superview
        }
        return ""
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
        case "swiftui": return "SwiftUI hosted in UIKit"
        case "uikit-in-swiftui": return "UIKit inside SwiftUI"
        default: return "UIKit"
        }
    }

}
#endif
#endif
