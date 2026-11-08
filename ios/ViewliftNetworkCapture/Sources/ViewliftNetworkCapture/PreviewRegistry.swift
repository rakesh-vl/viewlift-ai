#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import SwiftUI

/// Register SwiftUI views so agents can render them on a device and compare them to baselines.
///
/// Call from Debug code only, for example right after `CaptureService.shared.start()`:
/// ```swift
/// ViewliftPreviews.register("Login", states: ["empty", "error"]) { state in
///     LoginView(viewModel: .preview(state))
/// }
/// ```
public enum ViewliftPreviews {
    public static func register<Content: View>(
        _ name: String,
        @ViewBuilder content: @escaping @MainActor () -> Content
    ) {
        PreviewRegistry.shared.register(name, states: [PreviewRegistry.defaultState]) { _ in
            AnyView(content())
        }
    }

    public static func register<Content: View>(
        _ name: String,
        states: [String],
        @ViewBuilder content: @escaping @MainActor (String) -> Content
    ) {
        PreviewRegistry.shared.register(name, states: states) { state in
            AnyView(content(state))
        }
    }

    public static func unregister(_ name: String) {
        PreviewRegistry.shared.remove(name)
    }
}

/// Thread-safe store of registered previews. Names keep registration order.
final class PreviewRegistry: @unchecked Sendable {
    static let shared = PreviewRegistry()
    static let defaultState = "default"
    static let maxPreviews = 500
    static let maxStates = 50

    struct Entry {
        let name: String
        let states: [String]
        let make: @MainActor (String) -> AnyView
    }

    private let lock = NSLock()
    private var entries: [String: Entry] = [:]
    private var order: [String] = []

    func register(_ name: String, states: [String], make: @escaping @MainActor (String) -> AnyView) {
        let key = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(120))
        guard !key.isEmpty else { return }
        var cleaned: [String] = []
        for state in states.map({ String($0.prefix(80)) }) where !state.isEmpty && !cleaned.contains(state) {
            cleaned.append(state)
        }
        let entry = Entry(
            name: key,
            states: cleaned.isEmpty ? [Self.defaultState] : Array(cleaned.prefix(Self.maxStates)),
            make: make
        )
        lock.lock()
        defer { lock.unlock() }
        if entries[key] == nil {
            guard order.count < Self.maxPreviews else { return }
            order.append(key)
        }
        entries[key] = entry
    }

    func remove(_ name: String) {
        lock.lock()
        defer { lock.unlock() }
        entries[name] = nil
        order.removeAll { $0 == name }
    }

    func entry(_ name: String) -> Entry? {
        lock.lock()
        defer { lock.unlock() }
        return entries[name]
    }

    func list() -> [String: JSONValue] {
        lock.lock()
        let rows = order.compactMap { entries[$0] }
        lock.unlock()
        return [
            "previews": .array(rows.map { entry in
                .object(["name": .string(entry.name), "states": .array(entry.states.map(JSONValue.string))])
            }),
            "count": .number(Double(rows.count))
        ]
    }

    func removeAll() {
        lock.lock()
        defer { lock.unlock() }
        entries = [:]
        order = []
    }
}
#endif
