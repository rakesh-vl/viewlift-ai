#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(SwiftUI)
import SwiftUI
#endif

public final class SwiftUIRenderTracker: @unchecked Sendable {
    public static let shared = SwiftUIRenderTracker()

    private let lock = NSLock()
    private var counts: [String: Int] = [:]

    private init() {}

    public func record(_ name: String) {
        lock.lock()
        counts[name, default: 0] += 1
        lock.unlock()
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let current = counts
        lock.unlock()
        let rows = current
            .sorted { $0.value > $1.value }
            .map { name, count in
                JSONValue.object([
                    "name": .string(name),
                    "renders": .number(Double(count))
                ])
            }
        return [
            "components": .array(rows),
            "coverage": .string("Only views with viewliftRenderProbe are counted")
        ]
    }
}

#if canImport(SwiftUI)
private struct ViewliftRenderProbe: ViewModifier {
    let name: String

    func body(content: Content) -> some View {
        SwiftUIRenderTracker.shared.record(name)
        return content
    }
}

public extension View {
    func viewliftRenderProbe(_ name: String) -> some View {
        modifier(ViewliftRenderProbe(name: name))
    }
}
#endif
#endif
