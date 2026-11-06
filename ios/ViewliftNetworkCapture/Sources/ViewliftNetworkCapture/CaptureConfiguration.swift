import Foundation

/// Configuration for the local Viewlift AI SDK ingest receiver.
public struct CaptureConfiguration: Sendable {
    public var receiverURL: URL
    public var pairingToken: String
    public var debugKey: String
    public var pinnedCertificateSHA256: String
    public var maxRequestBodyBytes: Int
    public var maxResponseBodyBytes: Int
    public var extraRedactionKeys: [String]
    public var maxQueueDepth: Int
    public var debugModules: Set<DebugModule>
    public var performanceSampleInterval: TimeInterval

    public init(
        receiverURL: URL,
        pairingToken: String = "",
        debugKey: String = "",
        pinnedCertificateSHA256: String,
        maxRequestBodyBytes: Int = 262_144,
        maxResponseBodyBytes: Int = 262_144,
        extraRedactionKeys: [String] = [],
        maxQueueDepth: Int = 64,
        debugModules: Set<DebugModule> = [],
        performanceSampleInterval: TimeInterval = 1
    ) {
        self.receiverURL = receiverURL
        self.pairingToken = pairingToken
        self.debugKey = debugKey
        self.pinnedCertificateSHA256 = pinnedCertificateSHA256
            .replacingOccurrences(of: ":", with: "")
            .lowercased()
        self.maxRequestBodyBytes = max(0, maxRequestBodyBytes)
        self.maxResponseBodyBytes = max(0, maxResponseBodyBytes)
        self.extraRedactionKeys = extraRedactionKeys
        self.maxQueueDepth = max(1, maxQueueDepth)
        self.debugModules = debugModules
        self.performanceSampleInterval = max(0.25, performanceSampleInterval)
    }

    /// Bonjour discovery. The dashboard advertisement replaces host, port, fingerprint, and debug key.
    /// Scheme environment overrides: `VIEWLIFT_CAPTURE_HOST`, `VIEWLIFT_CAPTURE_PORT`,
    /// `VIEWLIFT_CAPTURE_FINGERPRINT`, `VIEWLIFT_CAPTURE_DEBUG_KEY`.
    public static func discovery(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> CaptureConfiguration? {
        let host = environment["VIEWLIFT_CAPTURE_HOST"] ?? "0.0.0.0"
        let port = Int(environment["VIEWLIFT_CAPTURE_PORT"] ?? "") ?? 9
        let fingerprint = environment["VIEWLIFT_CAPTURE_FINGERPRINT"] ?? ""
        let debugKey = environment["VIEWLIFT_CAPTURE_DEBUG_KEY"] ?? ""
        guard let receiverURL = URL(string: "https://\(host):\(port)") else {
            return nil
        }
        return CaptureConfiguration(
            receiverURL: receiverURL,
            debugKey: debugKey,
            pinnedCertificateSHA256: fingerprint,
            debugModules: Set(DebugModule.allCases)
        )
    }
}
