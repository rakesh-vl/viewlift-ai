import Foundation

/*
 Integration guidance (inspection-only SDK capture)

 Info.plist:
   NSLocalNetworkUsageDescription = "Viewlift AI uploads debug network captures to your Mac on the local network."

 Compile flags:
   - DEBUG builds: CaptureService.start is active.
   - Internal TestFlight: add Swift Active Compilation Condition VIEWLIFT_CAPTURE_ENABLED.
   - App Store / Release default: start()/log() are no-ops.

 SwiftUI (call before other networking):

   import ViewliftNetworkCapture
   #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
   CaptureService.shared.start(configuration: CaptureConfiguration(
       receiverURL: URL(string: "https://0.0.0.0:9")!,
       pinnedCertificateSHA256: ""
   ))
   // Host, port, fingerprint, and debug key arrive from the dashboard advertisement.
   // Optional overrides: VIEWLIFT_CAPTURE_HOST, VIEWLIFT_CAPTURE_PORT,
   // VIEWLIFT_CAPTURE_FINGERPRINT, VIEWLIFT_CAPTURE_DEBUG_KEY.
   #endif

 UIKit AppDelegate:

   func application(_ application: UIApplication,
                    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
       #if DEBUG || VIEWLIFT_CAPTURE_ENABLED
       CaptureService.shared.start(configuration: ...)
       #endif
       return true
   }

Supported: URLSession HTTP/HTTPS created after start (default/ephemeral).
Replay: URLSessionConfiguration.default with CaptureURLProtocol stripped (shared
cookies/cache/credentials; request properties preserved).
Not supported: WebSocket, WKWebView, Network.framework, third-party sockets,
background URLSession, forwarding custom URLSessionDelegate challenges/redirects.
Mutating proxy features require the Wi-Fi proxy path, not SDK mode.
Flows are labeled source SDK.
*/
enum IntegrationGuidance {
    static let localNetworkUsageDescription =
        "Viewlift AI uploads debug network captures to your Mac on the local network."
}
