//
//  ViewliftCaptureDebugService.swift
//  AppCMS
//
//  Debug-only capture. Release builds compile this file out.
//

#if DEBUG
import Foundation
import ViewliftNetworkCapture

/// Starts local SDK capture before the app creates its own URL sessions.
final class ViewliftCaptureDebugService {
    static let shared = ViewliftCaptureDebugService()

    private init() {}

    func start() {
        let env = ProcessInfo.processInfo.environment
        let host = env["VIEWLIFT_CAPTURE_HOST"] ?? "0.0.0.0"
        let port = Int(env["VIEWLIFT_CAPTURE_PORT"] ?? "") ?? 9
        let fingerprint = env["VIEWLIFT_CAPTURE_FINGERPRINT"] ?? ""
        let debugKey = env["VIEWLIFT_CAPTURE_DEBUG_KEY"] ?? ""
        guard let receiverURL = URL(string: "https://\(host):\(port)") else {
            return
        }
        let configuration = CaptureConfiguration(
            receiverURL: receiverURL,
            debugKey: debugKey,
            pinnedCertificateSHA256: fingerprint,
            debugModules: Set(DebugModule.allCases)
        )
        OTTDebugRegistry.register(AppCMSOTTDebugSnapshot.shared)
        CaptureService.shared.start(configuration: configuration)
    }
}
#endif
