#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import CoreGraphics
import Foundation

/// One SwiftUI view from the hosting view's debug data, in host coordinates.
struct SwiftUIDebugNode: Equatable {
    var type: String
    var frame: CGRect
    var children: [SwiftUIDebugNode]
    /// False when SwiftUI recorded no size. Containers then take the union of their children.
    var hasGeometry = true

    var name: String { SwiftUIDebugTree.displayName(for: type) }
}

/// Reads the SwiftUI view tree Xcode's view debugger shows (`ZStack`, `GeometryReader`, `Spacer`, ...).
/// The source is `_UIHostingView.makeViewDebugData()`, a private SwiftUI method that returns JSON.
/// SwiftUI records the tree only when `SWIFTUI_VIEW_DEBUG` is set before the first hosting view exists.
enum SwiftUIDebugTree {
    static let environmentKey = "SWIFTUI_VIEW_DEBUG"
    /// Bit mask of `_ViewDebug.Property`: type, transform, position, and size. Values are skipped
    /// because reflecting every view value is slow.
    static let recordedProperties = "29"
    static let maxDepth = 80

    private enum Property: Int {
        case type = 0
        case position = 3
        case size = 4
    }

    private static let transparent: Set<String> = [
        "AnyView", "TupleView", "Optional", "_ConditionalContent", "IDView", "EquatableView", "Group",
        "StaticIf", "_VariadicView.Tree", "Tree", "ViewBuilder", "_ViewModifier_Content", "_UnaryViewAdaptor",
        "ForEach", "_ViewListOutputs", "RootModifier", "HostingViewRoot", "EmptyModifier"
    ]

    /// SwiftUI internals Xcode shows under their public name.
    private static let aliases: [String: String] = [
        "KeyboardShortcutBindingBehavior": "Button",
        "ButtonBehavior": "Button",
        "StyledTextContentView": "Text"
    ]

    private static let modifierSuffixes = ["Modifier", "Effect", "Layout", "Behavior", "Environment", "Style"]

    private static let stackNames: [String: String] = [
        "VStack": "Vertical Stack",
        "HStack": "Horizontal Stack",
        "LazyVStack": "Lazy Vertical Stack",
        "LazyHStack": "Lazy Horizontal Stack",
        "_VStackLayout": "Vertical Stack",
        "_HStackLayout": "Horizontal Stack",
        "_ZStackLayout": "ZStack"
    ]

    /// Xcode shows these without their generic arguments. Others, such as `GeometryReader<...>`, keep them.
    private static let plainNames: Set<String> = [
        "ZStack", "Text", "Image", "Spacer", "Color", "Button", "Divider", "Rectangle", "RoundedRectangle",
        "Circle", "Capsule", "Ellipse", "ProgressView", "Toggle", "Slider", "ScrollView", "List", "Link",
        "Label", "NavigationStack", "NavigationView", "TabView", "LazyVGrid", "LazyHGrid", "Grid", "AsyncImage"
    ]

    private static let modifierNames: [(marker: String, name: String)] = [
        ("Background", "Background"),
        ("Overlay", "Overlay"),
        ("Padding", "Padding"),
        ("FrameLayout", "Frame"),
        ("FlexFrame", "Frame"),
        ("Opacity", "Opacity"),
        ("Offset", "Offset"),
        ("Clip", "Clip"),
        ("Mask", "Mask"),
        ("Shadow", "Shadow"),
        ("Scale", "Scale"),
        ("Rotation", "Rotation"),
        ("Aspect", "Aspect Ratio"),
        ("Position", "Position"),
        ("Fill", "Fill"),
        ("ForegroundStyle", "Foreground Style"),
        ("Transition", "Transition")
    ]

    /// Modifiers that only write environment, preferences, or callbacks. They never draw.
    private static let bookkeeping = [
        "Environment", "Preference", "Trait", "Appear", "Disappear", "Task", "Accessibility", "Identifier",
        "Transaction", "Animation", "Gesture", "OnChange", "Navigation", "Toolbar", "Focus", "Hidden",
        "ContentShape", "AllowsHitTesting", "Disabled", "Tag", "Sheet", "Alert", "Popover", "Receive",
        "SafeArea", "ScrollEdge", "PlatformItem", "Highlight", "ButtonShape", "ButtonAction", "Proxy",
        "LargeContent", "Renderer", "Hover", "Keyboard", "Drop", "Drag", "Context", "Interaction", "Empty"
    ]

    // MARK: Parsing

    static func parse(_ data: Data) -> [SwiftUIDebugNode] {
        guard let object = try? JSONSerialization.jsonObject(with: data) else { return [] }
        let roots: [Any]
        if let list = object as? [Any] {
            roots = list
        } else if let single = object as? [String: Any] {
            roots = [single]
        } else {
            return []
        }
        return roots.compactMap { node($0, depth: 0) }.flatMap(collapse)
    }

    private static func node(_ raw: Any, depth: Int) -> SwiftUIDebugNode? {
        guard depth < maxDepth, let fields = raw as? [String: Any] else { return nil }
        let properties = propertyMap(fields["properties"])
        let type = typeName(properties[Property.type.rawValue]) ?? ""
        let origin = pair(properties[Property.position.rawValue], keys: ("x", "y"))
        let size = pair(properties[Property.size.rawValue], keys: ("width", "height"))
        let children = (fields["children"] as? [Any] ?? []).compactMap { node($0, depth: depth + 1) }
        guard !type.isEmpty || !children.isEmpty else { return nil }
        let point = origin ?? (0, 0)
        let extent = size ?? (0, 0)
        return SwiftUIDebugNode(
            type: type,
            frame: CGRect(x: point.0, y: point.1, width: max(0, extent.0), height: max(0, extent.1)),
            children: children,
            hasGeometry: size != nil
        )
    }

    /// Properties arrive as `[{"id": 0, "attribute": {...}}]`. Some OS versions key them by id string.
    private static func propertyMap(_ raw: Any?) -> [Int: [String: Any]] {
        var map: [Int: [String: Any]] = [:]
        if let list = raw as? [Any] {
            for item in list {
                guard let entry = item as? [String: Any], let id = integer(entry["id"]) else { continue }
                map[id] = entry["attribute"] as? [String: Any] ?? entry
            }
        } else if let keyed = raw as? [String: Any] {
            for (key, value) in keyed {
                guard let id = Int(key), let attribute = value as? [String: Any] else { continue }
                map[id] = attribute
            }
        }
        return map
    }

    /// `readableType` first: `type` carries private context such as `(unknown context at $1d36e2424)`.
    private static func typeName(_ attribute: [String: Any]?) -> String? {
        guard let attribute else { return nil }
        for key in ["readableType", "type", "value"] {
            if let text = attribute[key] as? String, !text.isEmpty {
                return text
            }
        }
        return nil
    }

    /// Reads a point or size from an array, a keyed object, sub-attributes, or "(x, y)" text.
    private static func pair(_ attribute: [String: Any]?, keys: (String, String)) -> (Double, Double)? {
        guard let attribute else { return nil }
        if let found = pairValue(attribute["value"], keys: keys) {
            return found
        }
        if let subs = attribute["subattributes"] as? [Any] {
            var first: Double?
            var second: Double?
            for case let sub as [String: Any] in subs {
                let name = sub["name"] as? String ?? ""
                if name == keys.0 { first = number(sub["value"]) }
                if name == keys.1 { second = number(sub["value"]) }
            }
            if let first, let second { return (first, second) }
        }
        return pairValue(attribute, keys: keys)
    }

    private static func pairValue(_ raw: Any?, keys: (String, String)) -> (Double, Double)? {
        if let list = raw as? [Any], list.count >= 2, let first = number(list[0]), let second = number(list[1]) {
            return (first, second)
        }
        if let keyed = raw as? [String: Any], let first = number(keyed[keys.0]), let second = number(keyed[keys.1]) {
            return (first, second)
        }
        if let text = raw as? String {
            let numbers = text
                .split { !"0123456789.-e".contains($0) }
                .compactMap { Double($0) }
            if numbers.count >= 2 { return (numbers[0], numbers[1]) }
        }
        return nil
    }

    private static func number(_ raw: Any?) -> Double? {
        if let value = raw as? NSNumber { return value.doubleValue }
        if let value = raw as? String { return Double(value) }
        return nil
    }

    private static func integer(_ raw: Any?) -> Int? {
        if let value = raw as? NSNumber { return value.intValue }
        if let value = raw as? String { return Int(value) }
        return nil
    }
}

// MARK: Shaping

extension SwiftUIDebugTree {
    /// Drops wrappers that add nothing on screen, so the tree reads like Xcode's.
    static func collapse(_ node: SwiftUIDebugNode) -> [SwiftUIDebugNode] {
        var shaped = node
        shaped.children = node.children.flatMap(collapse)
        if !shaped.hasGeometry {
            // Inner content SwiftUI did not lay out on its own, for example a button's label chain.
            guard !shaped.children.isEmpty else { return [] }
            if isTransparent(shaped.type) || isModifier(headName(shaped.type)) { return shaped.children }
            shaped.frame = shaped.children.map(\.frame).reduce(CGRect.null) { $0.union($1) }
            shaped.hasGeometry = true
            return [shaped]
        }
        return isTransparent(shaped.type) ? shaped.children : [shaped]
    }

    static func isTransparent(_ type: String) -> Bool {
        let head = headName(type)
        if aliases[head] != nil { return false }
        if transparent.contains(head) { return true }
        if head == "ModifiedContent" {
            let modifier = lastArgument(of: type).map(headName) ?? ""
            return modifierLabel(modifier) == nil
        }
        return isModifier(head) && modifierLabel(head) == nil
    }

    /// Modifier types SwiftUI records as their own debug nodes, such as `_PaddingLayout`.
    static func isModifier(_ head: String) -> Bool {
        if aliases[head] != nil || stackNames[head] != nil { return false }
        return head.hasPrefix("_") || modifierSuffixes.contains { head.hasSuffix($0) }
    }

    /// Short name for the tree, for example `VStack<...>` becomes `Vertical Stack`.
    static func displayName(for type: String) -> String {
        let head = headName(type)
        if let alias = aliases[head] { return alias }
        if let stack = stackNames[head] { return stack }
        if plainNames.contains(head) { return head }
        if head == "ModifiedContent" {
            let modifier = lastArgument(of: type).map(headName) ?? ""
            return modifierLabel(modifier) ?? cleaned(modifier)
        }
        if isModifier(head) {
            return modifierLabel(head) ?? cleaned(head)
        }
        let readable = stripModules(type)
        return readable.count > 80 ? String(readable.prefix(77)) + "..." : readable
    }

    static func modifierLabel(_ modifier: String) -> String? {
        if transparent.contains(modifier) || bookkeeping.contains(where: { modifier.contains($0) }) { return nil }
        if let match = modifierNames.first(where: { modifier.contains($0.marker) }) { return match.name }
        return modifier.isEmpty ? nil : cleaned(modifier)
    }

    /// `SwiftUI.VStack<SwiftUI.TupleView<...>>` becomes `VStack`.
    static func headName(_ type: String) -> String {
        let head = type.split(separator: "<", maxSplits: 1).first.map(String.init) ?? type
        let trimmed = head.trimmingCharacters(in: .whitespaces)
        if trimmed.hasSuffix("_VariadicView.Tree") { return "_VariadicView.Tree" }
        return trimmed.split(separator: ".").last.map(String.init) ?? trimmed
    }

    static func stripModules(_ type: String) -> String {
        let unnamed = type.replacingOccurrences(
            of: #"\(unknown context at \$[0-9a-fA-F]+\)\.?"#,
            with: "",
            options: .regularExpression
        )
        var output = ""
        var token = ""
        for character in unnamed {
            if character.isLetter || character.isNumber || character == "_" {
                token.append(character)
            } else if character == "." {
                token = ""
            } else {
                output += token
                token = ""
                output.append(character)
            }
        }
        return output + token
    }

    private static func cleaned(_ modifier: String) -> String {
        var name = modifier
        while name.hasPrefix("_") { name.removeFirst() }
        for suffix in ["Modifier", "Effect", "Layout"] where name.hasSuffix(suffix) && name.count > suffix.count {
            name.removeLast(suffix.count)
        }
        return name
    }

    /// Last top-level generic argument: the modifier in `ModifiedContent<Content, Modifier>`.
    private static func lastArgument(of type: String) -> String? {
        guard let open = type.firstIndex(of: "<"), type.hasSuffix(">") else { return nil }
        let inner = type[type.index(after: open)..<type.index(before: type.endIndex)]
        var depth = 0
        var lastSplit = inner.startIndex
        for index in inner.indices {
            switch inner[index] {
            case "<", "(": depth += 1
            case ">", ")": depth -= 1
            case "," where depth == 0: lastSplit = inner.index(after: index)
            default: break
            }
        }
        let argument = inner[lastSplit...].trimmingCharacters(in: .whitespaces)
        return argument.isEmpty ? nil : argument
    }

    /// Asks SwiftUI to record its view tree. Must run before the first hosting view is created.
    /// An existing value, for example from the Xcode scheme, wins.
    static func enableRecording() {
        guard getenv(environmentKey) == nil else { return }
        setenv(environmentKey, recordedProperties, 0)
    }
}
#endif
