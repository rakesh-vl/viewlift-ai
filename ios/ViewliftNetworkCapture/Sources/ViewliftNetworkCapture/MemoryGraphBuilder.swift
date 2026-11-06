#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Object graph of tracked objects and the app objects they reference, two hops deep.
enum MemoryGraphBuilder {
    static let maxNodes = 140
    static let maxHops = 2

    @MainActor
    static func build(live: [(MemoryTrackedRecord, AnyObject)], leaked: Set<String>) -> [String: JSONValue] {
        var graph = GraphAccumulator(leaked: leaked)
        var queue: [(object: AnyObject, hop: Int)] = []
        for (record, object) in live {
            graph.add(object, record: record)
            queue.append((object, 0))
        }
        var cursor = 0
        while cursor < queue.count {
            let (object, hop) = queue[cursor]
            cursor += 1
            let from = RetentionInspector.objectId(object)
            for reference in RetentionInspector.references(of: object) {
                let target = reference.target
                let targetId = RetentionInspector.objectId(target)
                let isNew = !graph.contains(targetId)
                if isNew {
                    guard graph.count < maxNodes, worthShowing(target) else { continue }
                    graph.add(target, record: nil)
                    if hop + 1 < maxHops, !RetentionInspector.isSystemClass(RetentionInspector.typeName(target)) {
                        queue.append((target, hop + 1))
                    }
                }
                graph.link(from: from, to: targetId, label: reference.label)
            }
        }
        return [
            "nodes": .array(graph.nodes),
            "edges": .array(graph.edges),
            "truncated": .bool(graph.count >= maxNodes),
            "coverage": .string(
                "View controllers from viewDidLoad, objects registered with track(_:), and the app objects "
                    + "they hold two hops deep. Swift reflection cannot tell weak from strong references."
            )
        ]
    }

    private static func worthShowing(_ object: AnyObject) -> Bool {
        let name = RetentionInspector.typeName(object)
        return !RetentionInspector.isSystemClass(name) || HeapCensus.notableSystemClasses.contains(name)
    }
}

private struct GraphAccumulator {
    let leaked: Set<String>
    private(set) var nodes: [JSONValue] = []
    private(set) var edges: [JSONValue] = []
    private var ids: Set<String> = []
    private var linked: Set<String> = []

    init(leaked: Set<String>) {
        self.leaked = leaked
    }

    var count: Int { ids.count }

    func contains(_ id: String) -> Bool {
        ids.contains(id)
    }

    mutating func add(_ object: AnyObject, record: MemoryTrackedRecord?) {
        let id = RetentionInspector.objectId(object)
        guard ids.insert(id).inserted else { return }
        let name = record?.className ?? RetentionInspector.typeName(object)
        var node: [String: JSONValue] = [
            "id": .string(id),
            "class": .string(name),
            "tracked": .bool(record != nil),
            "leaked": .bool(leaked.contains(id)),
            "system": .bool(RetentionInspector.isSystemClass(name))
        ]
        if let record {
            node["context"] = .string(record.context)
            node["screen"] = .string(record.screen)
            node["age_seconds"] = .number(Date().timeIntervalSince(record.createdAt))
        }
        nodes.append(.object(node))
    }

    mutating func link(from: String, to: String, label: String) {
        guard from != to, linked.insert("\(from)>\(to)>\(label)").inserted else { return }
        edges.append(.object(["from": .string(from), "to": .string(to), "label": .string(label)]))
    }
}

/// Shapes heap census rows for the dashboard, with growth since the previous snapshot.
enum HeapCensusFormatter {
    static let maxRows = 150

    static func payload(
        rows: [HeapCensus.Row],
        previous: [String: Int],
        duration: TimeInterval
    ) -> [String: JSONValue] {
        let sorted = rows.sorted { left, right in
            let leftDelta = left.count - (previous[left.className] ?? 0)
            let rightDelta = right.count - (previous[right.className] ?? 0)
            return leftDelta != rightDelta ? leftDelta > rightDelta : left.count > right.count
        }
        let table = sorted.prefix(maxRows).map { row -> JSONValue in
            .object([
                "class": .string(row.className),
                "count": .number(Double(row.count)),
                "delta": .number(Double(row.count - (previous[row.className] ?? 0))),
                "bytes": .number(Double(row.count * row.instanceBytes))
            ])
        }
        return [
            "classes": .array(table),
            "class_count": .number(Double(rows.count)),
            "instance_count": .number(Double(rows.reduce(0) { $0 + $1.count })),
            "baseline": .bool(previous.isEmpty),
            "duration_ms": .number(duration * 1_000),
            "footprint_bytes": .number(Double(MemoryFootprint.current())),
            "note": .string(
                "Live instances of app classes found by walking the malloc heap. "
                    + "Bytes are instance sizes only, not buffers they own. Delta is growth since the last snapshot."
            )
        ]
    }
}
#endif
