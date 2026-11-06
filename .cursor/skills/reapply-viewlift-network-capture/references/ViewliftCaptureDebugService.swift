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
        OTTDebugRegistry.register(AppCMSOTTDebugSnapshot.shared)
        CaptureService.shared.start()
    }
}
#endif
