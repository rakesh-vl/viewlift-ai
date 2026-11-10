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

    private var sessionWatch: Task<Void, Never>?

    func start() {
        OTTDebugRegistry.register(AppCMSOTTDebugSnapshot.shared)
        AppCMSOTTDebugSnapshot.shared.publishCaptureSession()
        sessionWatch?.cancel()
        sessionWatch = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                AppCMSOTTDebugSnapshot.shared.publishCaptureSession()
            }
        }
        CaptureService.shared.start()
    }
}
#endif
