#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// One dashboard finding produced by a host diagnostic rule.
public struct OTTDebugFinding: Equatable, Sendable {
    public enum Severity: String, Sendable {
        case info
        case warning
        case error
    }

    public let id: String
    public let severity: Severity
    public let title: String
    public let evidence: [String: JSONValue]
    public let hint: String

    public init(
        id: String,
        severity: Severity,
        title: String,
        evidence: [String: JSONValue] = [:],
        hint: String = ""
    ) {
        self.id = id
        self.severity = severity
        self.title = title
        self.evidence = evidence
        self.hint = hint
    }

    public var json: JSONValue {
        .object([
            "id": .string(id),
            "severity": .string(severity.rawValue),
            "title": .string(title),
            "evidence": .object(evidence),
            "hint": .string(hint)
        ])
    }
}

/// One input the dashboard renders in an action form.
public struct OTTDebugActionField: Equatable, Sendable {
    public enum Kind: String, Sendable {
        case text
        case number
        case bool
        case select
    }

    public let name: String
    public let label: String
    public let kind: Kind
    public let options: [String]
    public let required: Bool

    public init(name: String, label: String, kind: Kind = .text, options: [String] = [], required: Bool = true) {
        self.name = name
        self.label = label
        self.kind = kind
        self.options = options
        self.required = required
    }

    var json: JSONValue {
        .object([
            "name": .string(name),
            "label": .string(label),
            "kind": .string(kind.rawValue),
            "options": .array(options.map(JSONValue.string)),
            "required": .bool(required)
        ])
    }
}

/// Ready-made `OTTDebugCommandHandling` for host apps.
/// Register sections, actions and diagnostic rules as closures; builders run on the main actor.
public final class OTTDebugHost: OTTDebugCommandHandling, @unchecked Sendable {
    public typealias Object = [String: JSONValue]
    public typealias SectionBuilder = @MainActor (Object) -> Object
    public typealias ActionRunner = @MainActor (Object) async throws -> Object
    public typealias Rule = @MainActor () -> OTTDebugFinding?

    private struct Section {
        let name: String
        let title: String
        let group: String
        let build: SectionBuilder
    }

    private struct Action {
        let name: String
        let title: String
        let note: String
        let fields: [OTTDebugActionField]
        let run: ActionRunner
    }

    public let ottDebugProviderName: String
    public let title: String
    private let lock = NSLock()
    private var sections: [Section] = []
    private var actions: [Action] = []
    private var rules: [Rule] = []
    private var observers: [NSObjectProtocol] = []

    public init(provider: String, title: String) {
        ottDebugProviderName = provider
        self.title = title
    }

    deinit {
        observers.forEach(NotificationCenter.default.removeObserver)
    }

    @discardableResult
    public func section(
        _ name: String,
        title: String,
        group: String = "",
        _ build: @escaping SectionBuilder
    ) -> Self {
        locked { sections.append(Section(name: name, title: title, group: group, build: build)) }
        return self
    }

    @discardableResult
    public func action(
        _ name: String,
        title: String,
        note: String = "",
        fields: [OTTDebugActionField] = [],
        _ run: @escaping ActionRunner
    ) -> Self {
        locked { actions.append(Action(name: name, title: title, note: note, fields: fields, run: run)) }
        return self
    }

    @discardableResult
    public func diagnostic(_ rule: @escaping Rule) -> Self {
        locked { rules.append(rule) }
        return self
    }

    /// Pushes `ott/changed` for `sections` whenever `name` is posted.
    @discardableResult
    public func observe(_ name: Notification.Name, sections: [String]) -> Self {
        let token = NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { _ in
            sections.forEach { OTTDebugRegistry.notifyChanged(section: $0) }
        }
        locked { observers.append(token) }
        return self
    }

    /// Makes this host the active `ott.*` handler. The registry holds it weakly; keep a strong reference.
    public func register() {
        OTTDebugRegistry.register(handler: self)
    }

    public func ottDebugSections() -> [String] {
        locked { sections.map(\.name) }
    }

    public func ottDebugActions() -> [String] {
        locked { actions.map(\.name) }
    }

    public func ottDebugSection(_ name: String, arguments: Object) async -> Object? {
        guard let section = locked({ sections.first { $0.name == name } }) else { return nil }
        return await section.build(arguments)
    }

    public func ottDebugDiagnostics() async -> [JSONValue] {
        let current = locked { rules }
        var findings: [JSONValue] = []
        for rule in current {
            if let finding = await rule() {
                findings.append(finding.json)
            }
        }
        return findings
    }

    public func ottDebugPerform(_ action: String, payload: Object) async throws -> Object {
        guard let match = locked({ actions.first { $0.name == action } }) else {
            throw OTTDebugError.unknownAction(action)
        }
        return try await match.run(payload)
    }

    public func ottDebugManifest() -> Object {
        locked {
            var forms: Object = [:]
            var notes: Object = [:]
            var titles: Object = [:]
            for action in actions {
                forms[action.name] = .array(action.fields.map(\.json))
                titles[action.name] = .string(action.title)
                if !action.note.isEmpty {
                    notes[action.name] = .string(action.note)
                }
            }
            let pages = sections.map { section -> JSONValue in
                .object([
                    "name": .string(section.name),
                    "title": .string(section.title),
                    "group": .string(section.group)
                ])
            }
            return [
                "title": .string(title),
                "pages": .array(pages),
                "action_forms": .object(forms),
                "action_titles": .object(titles),
                "action_notes": .object(notes)
            ]
        }
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}
#endif
