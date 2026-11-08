#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import CoreGraphics
import Foundation

/// Conversions that never trap. `Int(Double)` crashes on NaN, infinity, and values outside `Int`.
/// Frames, scales, and payload numbers can all be any of those, so the SDK converts through here.
extension Int {
    /// Largest magnitude `init(safe:)` returns. Two such values multiply without overflowing `Int`.
    static let safeLimit = Int(Int32.max)

    /// Rounds toward zero and clamps to `±safeLimit`. NaN returns `fallback`.
    init(safe value: Double, fallback: Int = 0) {
        guard !value.isNaN else {
            self = fallback
            return
        }
        let limit = Double(Int.safeLimit)
        self = Int(Swift.min(limit, Swift.max(-limit, value)))
    }

    init(safe value: CGFloat, fallback: Int = 0) {
        self.init(safe: Double(value), fallback: fallback)
    }
}

enum SafeNumber {
    /// Whole numbers print without a decimal, others with one. Huge or non-finite values never convert to `Int`.
    static func text(_ value: Double) -> String {
        guard value.isFinite else { return value.isNaN ? "nan" : (value < 0 ? "-inf" : "inf") }
        let rounded = (value * 10).rounded() / 10
        if rounded == rounded.rounded(), abs(rounded) < 1e15 { return String(Int(rounded)) }
        return String(format: "%.1f", rounded)
    }
}
#endif
