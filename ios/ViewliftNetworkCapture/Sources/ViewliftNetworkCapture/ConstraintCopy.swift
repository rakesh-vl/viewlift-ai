#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// The parts of one constraint that a person needs, without pointers or class dumps.
struct ConstraintFacts: Equatable {
    var attribute: String
    var relation: String
    var constant: Double
    var otherName: String
    var otherAttribute: String
    var hasOther: Bool
    var multiplier: Double
    var contentSize: Bool
    var hugging: Int
    var compression: Int
    var priority: Int
    var active: Bool
}

/// Turns a constraint into a sentence. "Width is 212.3 pt. Content size. Hugging 251."
enum ConstraintCopy {
    static func title(_ facts: ConstraintFacts) -> String {
        facts.attribute
    }

    static func summary(_ facts: ConstraintFacts) -> String {
        let verb = verb(facts.relation)
        let own = "\(facts.attribute) \(verb)"
        guard facts.hasOther else {
            return "\(own) \(points(facts.constant)) pt"
        }
        let other = "\(facts.otherName)'s \(facts.otherAttribute.lowercased())"
        if abs(facts.multiplier - 1) > 0.001 {
            return scaled(own, other: other, facts: facts)
        }
        guard abs(facts.constant) >= 0.05 else { return "\(own) \(other)" }
        let direction = facts.constant > 0 ? "plus" : "minus"
        return "\(own) \(other), \(direction) \(points(abs(facts.constant))) pt"
    }

    static func note(_ facts: ConstraintFacts) -> String {
        var parts: [String] = []
        if facts.contentSize {
            parts.append("Content size")
            parts.append("Hugging \(facts.hugging)")
            parts.append("Compression resistance \(facts.compression)")
        }
        if facts.priority < 1000 {
            parts.append("Priority \(facts.priority), so a required constraint can override it")
        }
        if !facts.active {
            parts.append("Not active")
        }
        return parts.joined(separator: ". ")
    }

    private static func scaled(_ own: String, other: String, facts: ConstraintFacts) -> String {
        let factor = "\(points(facts.multiplier)) × \(other)"
        guard abs(facts.constant) >= 0.05 else { return "\(own) \(factor)" }
        let direction = facts.constant > 0 ? "plus" : "minus"
        return "\(own) \(factor), \(direction) \(points(abs(facts.constant))) pt"
    }

    private static func verb(_ relation: String) -> String {
        switch relation {
        case "min": return "is at least"
        case "max": return "is at most"
        default: return "is"
        }
    }

    private static func points(_ value: Double) -> String {
        SafeNumber.text(value)
    }
}
#endif
