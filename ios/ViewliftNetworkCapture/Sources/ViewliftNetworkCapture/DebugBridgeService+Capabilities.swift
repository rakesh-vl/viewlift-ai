#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Capabilities the app registers with, derived from the enabled debug modules and platform.
extension DebugBridgeService {
    static func capabilities(_ config: CaptureConfiguration) -> [String: DebugCapability] {
        var out = Dictionary(
            uniqueKeysWithValues: DebugModule.allCases.map {
                ($0.rawValue, DebugCapability.unsupported)
            }
        )
        for module in config.debugModules {
            out[module.rawValue] = .available
        }
        for module in [DebugModule.memory, .swiftUI, .macro] where config.debugModules.contains(module) {
            out[module.rawValue] = .partial
        }
        #if os(tvOS)
        // tvOS local notifications can only set the app badge.
        if config.debugModules.contains(.push) {
            out[DebugModule.push.rawValue] = .partial
        }
        #endif
        out["network_shaping"] = config.captureNetwork ? .available : .unsupported
        #if canImport(UIKit)
        // Gestures without a public trigger (drags, raw touches) need the UI runner or simulator.
        out["ui_automation"] = config.debugModules.contains(.ui) ? .partial : .unsupported
        #else
        out["ui_automation"] = .unsupported
        out[DebugModule.preview.rawValue] = config.debugModules.contains(.preview) ? .partial : .unsupported
        #endif
        return out
    }
}
#endif
