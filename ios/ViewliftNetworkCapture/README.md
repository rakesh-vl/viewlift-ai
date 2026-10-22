# ViewliftNetworkCapture

Debug-only iOS/tvOS HTTP capture and structured log upload for the local Viewlift
AI dashboard. No Wi-Fi proxy or installed CA is required.

## Requirements

- iOS 16+ or tvOS 16+
- Swift 5.9+
- Mac and device on the same trusted LAN

## Add package

Add this directory as a local Swift package. For a remote repository, use that
repository URL and select the `ViewliftNetworkCapture` product.

Add this key to the app's `Info.plist`:

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Debug builds send network captures to the local Viewlift dashboard.</string>
```

## Start before app networking

Get receiver URL, one-use token, and SHA-256 fingerprint from the dashboard's
**Setup** page.

```swift
import ViewliftNetworkCapture

#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
CaptureService.shared.start(
    configuration: CaptureConfiguration(
        receiverURL: URL(string: "https://192.168.1.10:8742")!,
        pairingToken: "<one-use-token>",
        pinnedCertificateSHA256: "<sha256-hex>"
    )
)
#endif
```

Call this from the SwiftUI app initializer or
`application(_:didFinishLaunchingWithOptions:)` before constructing networking
services.

```swift
LogService.shared.log(
    level: .info,
    subsystem: "Playback",
    message: "Manifest loaded",
    requestId: requestId
)
```

For an internal TestFlight build, add `VIEWLIFT_CAPTURE_ENABLED` under Swift Active
Compilation Conditions. Never add it to App Store builds.

## Boundaries

Supported: HTTP/HTTPS requests from `URLSession` default/ephemeral configurations
created after capture starts.

Replay uses a `URLSessionConfiguration.default` copy with only
`CaptureURLProtocol` removed so shared cookies, URL cache, and credential storage
remain available, and the original request’s method, headers, body, and timeout
are forwarded.

Not supported / unavoidable limits:

- WebSocket frames, `WKWebView`, Network.framework, third-party socket stacks
- Background `URLSession` configurations (not inserted into those configs)
- Custom `URLSessionDelegate` authentication challenges and redirect policies on
  the app’s session are not forwarded through the interceptor’s replay session
- SDK mode is inspection-only (no breakpoints / map / rewrite / throttle)

Request/response bodies are capped. Sensitive headers and nested JSON secrets are
redacted on-device. Upload uses a one-use pairing token, short-lived bearer session,
and exact SHA-256 certificate pin.
