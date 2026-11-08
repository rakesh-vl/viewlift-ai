#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Turns Swift and Objective-C type names into labels a person can read,
/// and tells a hosting view apart from a normal UIKit view.
enum UITypeName {
    private static let hostMarkers = ["HostingView", "HostingController"]
    private static let noise: Set<String> = [
        "ModifiedContent", "AnyView", "TupleView", "Optional", "Group", "VStack", "HStack", "ZStack",
        "ForEach", "EquatableView", "IDView", "NavigationStack", "NavigationView", "ScrollView",
        "GeometryReader", "Color", "Text", "Image", "Button", "Spacer", "Divider", "EmptyView",
        "List", "Form", "Section", "LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid",
        "Overlay", "Background", "Label", "Link", "Picker", "Toggle", "Slider", "Stepper",
        "ProgressView", "TabView", "SecureField", "TextField", "DatePicker"
    ]

    static func isSwiftUIHost(_ typeName: String) -> Bool {
        hostMarkers.contains { typeName.contains($0) }
    }

    /// `selfIsHost` wins. A hosting ancestor or a hosting controller means UIKit drawn for SwiftUI.
    static func role(selfIsHost: Bool, ancestorIsHost: Bool, controllerIsHost: Bool) -> String {
        if selfIsHost {
            return "swiftui"
        }
        if ancestorIsHost || controllerIsHost {
            return "uikit-in-swiftui"
        }
        return "uikit"
    }

    static func moduleAndShort(qualified: String, described: String) -> (module: String, short: String) {
        let head = qualified.split(separator: "<", maxSplits: 1).first.map(String.init) ?? qualified
        let bits = head.split(separator: ".")
        let module = bits.count >= 2 ? String(bits[bits.count - 2]) : ""
        if described.contains("<") {
            return (module, String(described.prefix(80)))
        }
        let describedHead = described.split(separator: ".").last.map(String.init) ?? described
        let short = describedHead.isEmpty ? (bits.last.map(String.init) ?? head) : describedHead
        return (module, String(short.prefix(80)))
    }

    /// App type inside `UIHostingController<...>` or `_UIHostingView<...>`, skipping SwiftUI wrappers.
    /// `PlayerControlsHostingView` with no generic falls back to `PlayerControls`.
    static func appViewName(in typeName: String) -> String? {
        if let argument = hostArgument(typeName) {
            let head = argument.split(separator: "<", maxSplits: 1).first.map(String.init) ?? argument
            let leaf = head.split(separator: ".").last.map(String.init) ?? head
            if isReadable(leaf) {
                return leaf
            }
            if let nested = firstReadable(in: argument) {
                return nested
            }
        }
        return hostStem(typeName)
    }

    /// `PlayerControlsHostingView` → `PlayerControls`. System hosts such as `UIHostingView` stay nil.
    static func hostStem(_ typeName: String) -> String? {
        let leaf = typeName.split(separator: ".").last.map(String.init) ?? typeName
        let head = leaf.split(separator: "<", maxSplits: 1).first.map(String.init) ?? leaf
        for suffix in ["HostingController", "HostingView"] where head.hasSuffix(suffix) {
            let stem = String(head.dropLast(suffix.count))
            if isReadable(stem) {
                return stem
            }
        }
        return nil
    }

    /// Spoken name for a control. The label wins, then the value, then the trait ("Button").
    static func controlTitle(label: String, value: String, fallback: String) -> String {
        let spoken = label.trimmingCharacters(in: .whitespacesAndNewlines)
        if !spoken.isEmpty {
            return String(spoken.prefix(80))
        }
        let extra = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if !extra.isEmpty {
            return String(extra.prefix(80))
        }
        return fallback
    }

    /// Name to show in the tree. A SwiftUI host prefers the SwiftUI view over the UIKit class.
    static func displayName(uikit: String, swiftui: String, role: String) -> String {
        if role == "swiftui", !swiftui.isEmpty {
            return swiftui
        }
        return uikit
    }

    private static func hostArgument(_ typeName: String) -> String? {
        let starts = hostMarkers.compactMap { marker -> String.Index? in
            guard let range = typeName.range(of: marker + "<") else { return nil }
            return range.upperBound
        }
        guard let start = starts.min() else { return nil }
        var depth = 1
        var cursor = start
        while cursor < typeName.endIndex {
            let character = typeName[cursor]
            if character == "<" {
                depth += 1
            } else if character == ">" {
                depth -= 1
                if depth == 0 {
                    return String(typeName[start..<cursor])
                }
            }
            cursor = typeName.index(after: cursor)
        }
        return nil
    }

    private static func firstReadable(in text: String) -> String? {
        let parts = text.split { character in
            !character.isLetter && !character.isNumber && character != "_"
        }
        return parts.map(String.init).first(where: isReadable)
    }

    private static func isReadable(_ name: String) -> Bool {
        guard let first = name.first, first.isUppercase, name.count > 2 else { return false }
        if name.hasPrefix("_") || name.hasPrefix("UI") || name.contains("Hosting") { return false }
        if noise.contains(name) || name.hasSuffix("Layout") || name.hasSuffix("Modifier") { return false }
        return true
    }
}
#endif
