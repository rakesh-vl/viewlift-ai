# Prompt: add Viewlift AI to any iOS or tvOS app

Copy everything below the line into the app repository's agent chat, or save it as that repository's `AGENTS.md`.

The package is added with Swift Package Manager. Do not copy the Swift sources into the app, do not vendor a framework, and do not add the dependency with CocoaPods or Carthage.

---

Connect this app to Viewlift AI so debug builds report to the local console and you can debug them through MCP.

Add the SDK with Swift Package Manager on every iOS and tvOS app target. iOS 16+ and tvOS 16+. Repeat the steps for each app target. Do not add it to a test target, a framework target, or a Release-only configuration.

1. Add the Swift package `https://github.com/rakesh-vl/viewlift-ai.git`.
   - Requirement: branch `main` (up to next major).
   - Product to link: `ViewliftNetworkCapture`.
   - Use Xcode's Swift package integration (`File > Add Package Dependencies`, or the `XCRemoteSwiftPackageReference` and `XCSwiftPackageProductDependency` entries in the project). The repository root `Package.swift` exposes the library.
   - Use the local package path `ios/ViewliftNetworkCapture` only when this checkout is the Viewlift AI repository and you are editing the package. Every other project uses the git URL.
   - Do not copy the Sources folder into the app. Do not add a binary target, a CocoaPod, or a Carthage dependency for this SDK.

2. Add these keys to each app target's Info.plist. Without `NSBonjourServices` the app cannot find the console.

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Viewlift AI uploads debug network captures to your Mac on the local network.</string>
<key>NSBonjourServices</key>
<array>
    <string>_viewliftdebug._tcp</string>
</array>
```

3. Call `start()` before the app creates a `URLSession` or a `URLSessionConfiguration`. Sessions created earlier are not captured. UIKit: the first lines of `application(_:didFinishLaunchingWithOptions:)`. SwiftUI: `App.init`.

```swift
import ViewliftNetworkCapture

#if DEBUG
CaptureService.shared.start()
#endif
```

`start()` belongs inside `#if DEBUG` only. Do not call it from Release, TestFlight, or App Store builds. Do not commit a pairing token, a certificate fingerprint, a receiver URL, or an auth token. `start()` finds the console over Bonjour.

4. If the app sends bearer tokens, add those header names and JSON key names to `CaptureConfiguration.extraRedactionKeys` and pass that configuration to `start(configuration:)`. Add the names only. Do not put token values in source.

5. Write `.cursor/mcp.json` in this repository (create `.cursor/` if needed) with one server:

```json
{
  "mcpServers": {
    "viewlift-ai": {
      "url": "http://127.0.0.1:8742/mcp"
    }
  }
}
```

Tell me to enable **viewlift-ai** in Cursor Settings → MCP. Do not add a second `viewlift-ai` server.

6. Build each iOS and tvOS app target in Debug. Confirm a Release build does not call `start()`.

The Viewlift AI console must already be running (`scripts/viewlift-ai.sh start` in the Viewlift AI repo) with the SDK receiver enabled on Setup. If this is the AppCMS / ViewliftOne app, use its existing `ViewliftCaptureDebugService` instead of calling `start()` directly.

After the app is online, debug through MCP. Call `list_apps` and pass that `client_id`. Use `triage_app`, `failed_requests`, `summarize_logs`, `get_flow`, and `app_inspect`. Ask before any tool that changes state (`confirm: true`). Never ask me to paste an auth token, cookie, or API key. Those values are masked on purpose.
