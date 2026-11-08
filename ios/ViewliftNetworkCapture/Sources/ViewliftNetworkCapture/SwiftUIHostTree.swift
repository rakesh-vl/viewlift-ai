#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
import ViewliftCaptureObjC

/// Turns a hosting view's SwiftUI debug tree into hierarchy nodes and places the hosting view's
/// UIKit subviews (representables, `View Host`) under the SwiftUI node that draws them.
@MainActor
enum SwiftUIHostTree {
    struct HostedChild {
        let frame: CGRect
        let node: JSONValue
    }

    struct Context {
        let host: UIView
        let hostName: String
        let hostId: String
        let labels: [SwiftUIControlNodes.Label]
    }

    private static let selector = NSSelectorFromString("makeViewDebugData")

    /// Nil when this SwiftUI build has no debug data or recording was off when the host was created.
    static func debugNodes(of host: UIView) -> [SwiftUIDebugNode]? {
        guard host.responds(to: selector) else { return nil }
        var data: Data?
        let raised = VLCCatchException {
            data = host.perform(selector)?.takeUnretainedValue() as? Data
        }
        guard raised == nil, let data, !data.isEmpty else { return nil }
        let nodes = SwiftUIDebugTree.parse(data)
        return nodes.isEmpty ? nil : nodes
    }

    static func merge(
        _ roots: [SwiftUIDebugNode],
        context: Context,
        hosted: [HostedChild],
        remaining: inout Int
    ) -> [JSONValue] {
        var flat: [(path: String, frame: CGRect)] = []
        flatten(roots, prefix: "", host: context.host, into: &flat)
        var placement: [String: [Int]] = [:]
        for (index, child) in hosted.enumerated() {
            guard let path = bestContainer(for: child.frame, in: flat) else { continue }
            placement[path, default: []].append(index)
        }
        var emitted = Set<Int>()
        var builder = Builder(context: context, hosted: hosted, placement: placement)
        var output: [JSONValue] = []
        for (index, root) in roots.enumerated() where remaining > 0 {
            output.append(builder.build(root, path: "\(index)", remaining: &remaining, emitted: &emitted))
        }
        for (index, child) in hosted.enumerated() where !emitted.contains(index) {
            output.append(child.node)
        }
        return output
    }

    private static func flatten(
        _ nodes: [SwiftUIDebugNode],
        prefix: String,
        host: UIView,
        into flat: inout [(path: String, frame: CGRect)]
    ) {
        for (index, node) in nodes.enumerated() {
            let path = prefix.isEmpty ? "\(index)" : "\(prefix).\(index)"
            flat.append((path, host.convert(node.frame, to: nil)))
            flatten(node.children, prefix: path, host: host, into: &flat)
        }
    }

    /// Smallest SwiftUI frame that contains the UIKit view. Pre-order, so a deeper node wins a tie.
    private static func bestContainer(for frame: CGRect, in flat: [(path: String, frame: CGRect)]) -> String? {
        var best: (path: String, area: CGFloat)?
        for entry in flat where entry.frame.insetBy(dx: -1, dy: -1).contains(frame) {
            let area = entry.frame.width * entry.frame.height
            if best == nil || area <= (best?.area ?? .infinity) {
                best = (entry.path, area)
            }
        }
        return best?.path
    }

    @MainActor
    private struct Builder {
        let context: Context
        let hosted: [HostedChild]
        let placement: [String: [Int]]

        func build(
            _ node: SwiftUIDebugNode,
            path: String,
            remaining: inout Int,
            emitted: inout Set<Int>
        ) -> JSONValue {
            remaining -= 1
            var children: [JSONValue] = []
            for (index, child) in node.children.enumerated() where remaining > 0 {
                children.append(build(child, path: "\(path).\(index)", remaining: &remaining, emitted: &emitted))
            }
            for index in placement[path] ?? [] where !emitted.contains(index) {
                emitted.insert(index)
                children.append(hosted[index].node)
            }
            let frame = context.host.convert(node.frame, to: nil)
            let kind = SwiftUIHostTree.kind(of: node.type)
            let name = node.name
            let text = kind == "view" ? "" : SwiftUIHostTree.label(for: frame, in: context.labels)
            var fields: [String: JSONValue] = [
                "id": .string("sui-\(context.hostId)-\(path)"),
                "class": .string(name),
                "display": .string(text.isEmpty ? name : "\(name) - \(String(text.prefix(60)))"),
                "type": .string(String(SwiftUIDebugTree.stripModules(node.type).prefix(240))),
                "role": .string("swiftui"),
                "kind": .string(kind),
                "synthetic": .bool(true),
                "swiftui_node": .bool(true),
                "x": .number(Double(frame.minX)),
                "y": .number(Double(frame.minY)),
                "width": .number(Double(frame.width)),
                "height": .number(Double(frame.height)),
                "alpha": .number(1),
                "hidden": .bool(false),
                "host_id": .string(context.hostId),
                "children": .array(children)
            ]
            if !text.isEmpty { fields["text"] = .string(String(text.prefix(160))) }
            if !context.hostName.isEmpty { fields["swiftui"] = .string(context.hostName) }
            return .object(fields)
        }
    }

    static func kind(of type: String) -> String {
        switch SwiftUIDebugTree.headName(type) {
        case "Text": return "text"
        case "Image", "AsyncImage": return "image"
        case "Button", "Link", "NavigationLink": return "button"
        case "Toggle", "Slider", "Picker", "Stepper", "TextField", "SecureField", "DatePicker": return "control"
        default: return "view"
        }
    }

    private static func label(for frame: CGRect, in labels: [SwiftUIControlNodes.Label]) -> String {
        labels.first { candidate in
            abs(candidate.frame.minX - frame.minX) < 3 && abs(candidate.frame.minY - frame.minY) < 3
                && abs(candidate.frame.width - frame.width) < 4 && abs(candidate.frame.height - frame.height) < 4
        }?.text ?? ""
    }
}
#endif
#endif
