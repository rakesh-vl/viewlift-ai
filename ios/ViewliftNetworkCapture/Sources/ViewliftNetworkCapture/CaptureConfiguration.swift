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
}
