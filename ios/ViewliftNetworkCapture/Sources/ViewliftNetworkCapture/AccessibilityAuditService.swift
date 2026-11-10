#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// One audit problem, tied to the element and its frame in window points.
struct AccessibilityFinding {
    enum Severity: String {
        case error, warning, info
    }

    var rule: String
    var severity: Severity
    var title: String
    var detail: String
    var fix: String
    var role = ""
    var label = ""
    var identifier = ""
    var frame: CGRect = .zero
    var viewId = ""

    func json(index: Int) -> JSONValue {
        .object([
            "id": .string("f\(index)"),
            "rule": .string(rule),
            "severity": .string(severity.rawValue),
            "title": .string(title),
            "detail": .string(detail),
            "fix": .string(fix),
            "element": .object([
                "role": .string(role),
                "label": .string(label),
                "identifier": .string(identifier)
            ]),
            "frame": .object([
                "x": .number(Double(frame.origin.x).rounded()),
                "y": .number(Double(frame.origin.y).rounded()),
                "width": .number(Double(frame.width).rounded()),
                "height": .number(Double(frame.height).rounded())
            ]),
            "view_id": .string(viewId)
        ])
    }
}

/// `accessibility.audit`: checks the key window the way VoiceOver and the Accessibility Inspector
/// see it, so SwiftUI content is covered, and returns a screenshot to outline each finding on.
enum AccessibilityAuditService {
    static let maxFindings = 300
    static let maxPerRule = 40
    static let screenshotBudget = 220_000

    static let interactiveRoles: Set<String> = [
        "button", "link", "cell", "switch", "slider", "textfield", "securetextfield",
        "textview", "searchfield", "segmentedcontrol", "tab"
    ]

    @MainActor
    static func audit() -> [String: JSONValue] {
        #if canImport(UIKit)
        guard let window = AutomationScreen.keyWindow() ?? AutomationScreen.windows().first else {
            return ["findings": .array([]), "count": .number(0), "detail": .string("No visible window.")]
        }
        let nodes = AccessibilityTreeService.nodes(in: [window])
        var findings = AccessibilityElementRules.check(nodes)
        findings += AccessibilityViewRules.check(window: window, nodes: nodes)
        let limited = limit(findings)
        var out: [String: JSONValue] = [
            "screen": .string(AutomationScreen.name()),
            "window": .object([
                "width": .number(Double(window.bounds.width)),
                "height": .number(Double(window.bounds.height))
            ]),
            "elements": .number(Double(nodes.count)),
            "findings": .array(limited.enumerated().map { $0.element.json(index: $0.offset) }),
            "count": .number(Double(limited.count)),
            "truncated": .bool(limited.count < findings.count),
            "counts": .object(Dictionary(
                uniqueKeysWithValues: [AccessibilityFinding.Severity.error, .warning, .info].map { severity in
                    (severity.rawValue, .number(Double(findings.filter { $0.severity == severity }.count)))
                }
            ))
        ]
        if let shot = BugReportService.jpeg(of: window, budget: screenshotBudget, edges: [1_000, 760, 560]) {
            out["screenshot"] = .object([
                "mime": .string("image/jpeg"),
                "base64": .string(shot.data.base64EncodedString())
            ])
        }
        return out
        #else
        return ["findings": .array([]), "count": .number(0)]
        #endif
    }

    /// Errors first, and no single rule may crowd out the rest.
    private static func limit(_ findings: [AccessibilityFinding]) -> [AccessibilityFinding] {
        let order: [AccessibilityFinding.Severity] = [.error, .warning, .info]
        var perRule: [String: Int] = [:]
        var out: [AccessibilityFinding] = []
        for severity in order {
            for item in findings where item.severity == severity {
                let used = perRule[item.rule, default: 0]
                guard used < maxPerRule, out.count < maxFindings else { continue }
                perRule[item.rule] = used + 1
                out.append(item)
            }
        }
        return out
    }
}

#if canImport(UIKit)
/// Rules over the accessibility elements VoiceOver reads.
@MainActor
enum AccessibilityElementRules {
    static func check(_ nodes: [AccessibilityNode]) -> [AccessibilityFinding] {
        var out: [AccessibilityFinding] = []
        var labels: [String: [AccessibilityNode]] = [:]
        for node in nodes {
            let info = node.info
            let interactive = AccessibilityAuditService.interactiveRoles.contains(info.role)
                || node.view is UIControl && node.isView
            if interactive {
                out += controlFindings(node)
                if !info.label.isEmpty {
                    labels[info.label.lowercased(), default: []].append(node)
                }
            } else if info.role == "image", info.label.isEmpty {
                out.append(finding(node, AccessibilityFinding(
                    rule: "image-label", severity: .warning,
                    title: "Image has no description",
                    detail: "VoiceOver reads this image as \"image\" with nothing else.",
                    fix: "Describe it with accessibilityLabel / .accessibilityLabel(\"…\"), "
                        + "or hide a decorative image with .accessibilityHidden(true)."
                )))
            }
        }
        for (label, matches) in labels where matches.count > 1 {
            guard let first = matches.first else { continue }
            out.append(finding(first, AccessibilityFinding(
                rule: "duplicate-label", severity: .info,
                title: "\(matches.count) controls are all called \"\(label)\"",
                detail: "VoiceOver users cannot tell these controls apart.",
                fix: "Add context, for example \"Play Episode 3\", or an accessibilityHint."
            )))
        }
        return out
    }

    private static func controlFindings(_ node: AccessibilityNode) -> [AccessibilityFinding] {
        let info = node.info
        var out: [AccessibilityFinding] = []
        if info.label.isEmpty, info.value.isEmpty {
            out.append(finding(node, AccessibilityFinding(
                rule: "missing-label", severity: .error,
                title: "\(roleName(info.role)) has no label",
                detail: "VoiceOver says only \"\(info.role)\", so the user cannot tell what it does.",
                fix: "Set accessibilityLabel (UIKit) or .accessibilityLabel(\"…\") (SwiftUI)."
            )))
        } else if looksLikeCode(info.label) {
            out.append(finding(node, AccessibilityFinding(
                rule: "code-label", severity: .warning,
                title: "Label looks like a code or file name: \"\(info.label)\"",
                detail: "This usually comes from an image name, not from a written label.",
                fix: "Give the control a human label with accessibilityLabel."
            )))
        } else if repeatsRole(info.label) {
            out.append(finding(node, AccessibilityFinding(
                rule: "label-repeats-role", severity: .info,
                title: "Label repeats the control type: \"\(info.label)\"",
                detail: "VoiceOver already says \"button\", so the user hears it twice.",
                fix: "Drop the words \"button\" or \"btn\" from the label."
            )))
        }
        #if os(iOS)
        if info.frame.width < 44 || info.frame.height < 44 {
            let size = "\(Int(info.frame.width))×\(Int(info.frame.height))"
            out.append(finding(node, AccessibilityFinding(
                rule: "small-target", severity: .warning,
                title: "Touch target is \(size) points",
                detail: "Apple recommends at least 44×44 points so the control is easy to hit.",
                fix: "Grow the tappable area, for example with contentEdgeInsets or .contentShape."
            )))
        }
        #endif
        return out
    }

    static func finding(_ node: AccessibilityNode, _ base: AccessibilityFinding) -> AccessibilityFinding {
        var item = base
        item.role = node.info.role
        item.label = node.info.label
        item.identifier = node.info.identifier
        item.frame = node.info.frame
        item.viewId = node.info.viewId
        return item
    }

    private static func roleName(_ role: String) -> String {
        role == "element" ? "Control" : role.prefix(1).uppercased() + role.dropFirst()
    }

    private static func looksLikeCode(_ label: String) -> Bool {
        guard !label.contains(" "), label.count > 3 else { return false }
        let lowered = label.lowercased()
        if [".png", ".jpg", ".pdf", "ic_", "icon_", "btn_"].contains(where: lowered.contains) { return true }
        if label.contains("_") { return true }
        // camelCase such as "playButtonIcon"; a single inner capital like "iPhone" is a real word.
        let startsLower = label.first?.isLowercase ?? false
        return startsLower && label.dropFirst().filter(\.isUppercase).count >= 2
    }

    private static func repeatsRole(_ label: String) -> Bool {
        let words = label.lowercased().split { !$0.isLetter }
        return words.contains("button") || words.contains("btn")
    }
}
#endif
#endif
