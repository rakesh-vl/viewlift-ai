# iOS SDK guide

`ViewliftNetworkCapture` is a Swift package that captures an app's `URLSession` traffic and logs and sends them to the Viewlift AI console over pinned HTTPS. It also runs the debug bridge that powers the Debug Suite and the MCP tools. No Wi-Fi proxy or installed CA is needed.

It is for debug builds only.

## Requirements

- iOS 16+ or tvOS 16+ (macOS 13 is declared only so `swift test` runs on a Mac).
- Swift 5.9+.
- The Mac and the device on the same trusted LAN or VPN, or a simulator on the same Mac.
- The console running with the SDK receiver enabled (**Setup**, "SDK receiver").

## Add the package

- **Git SPM** (shared branches): `https://github.com/rakesh-vl/viewlift-ai.git`, branch `main`, product `ViewliftNetworkCapture`. The repository root [Package.swift](../Package.swift) exposes the library.
- **Local path** (working on the package): add `ios/ViewliftNetworkCapture` as a local package.

## Info.plist

Both keys are required, on iOS and tvOS. Without `NSBonjourServices`, receiver discovery does not work.

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Viewlift AI uploads debug network captures to your Mac on the local network.</string>
<key>NSBonjourServices</key>
<array>
    <string>_viewliftdebug._tcp</string>
</array>
```

## Start capture

Start capture before the app creates any `URLSession` or `URLSessionConfiguration`. Sessions configured earlier are not intercepted.

### Option A: automatic discovery (recommended)

The console advertises `_viewliftdebug._tcp` over Bonjour with its address, port, certificate fingerprint and debug key. `start()` uses the `https://0.0.0.0:9` placeholder, an empty fingerprint, and every debug module. Discovery fills in the rest and re-pairs when the Mac's address changes.

```swift
import ViewliftNetworkCapture

#if DEBUG
CaptureService.shared.start()
#endif
```

Optional scheme environment overrides: `VIEWLIFT_CAPTURE_HOST`, `VIEWLIFT_CAPTURE_PORT`, `VIEWLIFT_CAPTURE_FINGERPRINT`, `VIEWLIFT_CAPTURE_DEBUG_KEY`. Without them, Bonjour discovery is used.

### Option B: fixed receiver

Copy the receiver URL, a pairing token and the SHA-256 fingerprint from **Setup**. Pairing tokens are single-use and expire after five minutes, so this suits one-off sessions. Use `debugKey` instead of `pairingToken` if you want the app to mint its own tokens.

```swift
#if DEBUG
CaptureService.shared.start(configuration: CaptureConfiguration(
    receiverURL: URL(string: "https://192.168.1.10:8743")!,
    pairingToken: "<one-use-token>",
    pinnedCertificateSHA256: "<sha256-hex>",
    debugModules: Set(DebugModule.allCases)
))
#endif
```

### Where to call it

SwiftUI:

```swift
@main
struct MyApp: App {
    init() {
        #if DEBUG
        CaptureService.shared.start()
        #endif
    }

    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
```

UIKit: put the same `CaptureService.shared.start()` call on the first lines of `application(_:didFinishLaunchingWithOptions:)`.

Pass `CaptureConfiguration` only for a fixed receiver or a smaller module set. AppCMS still wraps the call in a debug service so it can register its OTT snapshot before `start()`. See the [AppCMS integration](appcms-integration.md).

## CaptureConfiguration

| Field | Default | Notes |
|---|---|---|
| `receiverURL` | required on `CaptureConfiguration` | `start()` uses `https://0.0.0.0:9`. Pass a real `https://host:port` only for a fixed receiver. |
| `pairingToken` | `""` | One-use token from Setup |
| `debugKey` | `""` | Lets the SDK mint pairing tokens. Filled in by discovery. |
| `pinnedCertificateSHA256` | required on `CaptureConfiguration` | `start()` passes `""`. Discovery fills the hex fingerprint. Colons are allowed. |
| `maxRequestBodyBytes` | `262_144` | Bodies above this are truncated |
| `maxResponseBodyBytes` | `262_144` | |
| `extraRedactionKeys` | `[]` | Extra header names and JSON keys to redact, for example `["authorization", "access_token"]` |
| `maxQueueDepth` | `64` | In-memory queue; the oldest item is dropped when full |
| `debugModules` | `[]` on `CaptureConfiguration` | Empty disables the debug bridge. `start()` passes `Set(DebugModule.allCases)`. |
| `performanceSampleInterval` | `1` | Seconds, minimum 0.25 |
| `captureNetwork` | `true` | `false` stops `URLProtocol` capture while the debug bridge keeps running |
| `bodyContentTypes` | `[]` | Content-Type prefixes whose bodies are kept, for example `["application/json"]`. Empty keeps every body. |

Crashes: the SDK records fatal signals and uncaught exceptions, and the console shows them with the full stack. Signal crashes arrive on the next launch. Exceptions arrive as the app dies. A crash while Xcode's debugger is attached stops in Xcode and reaches neither the SDK nor the system crash report until you continue or detach. Run without the debugger to see it in the console.

Crash context and replay: while the `crash` module is on, the SDK writes what the app is doing to disk once a second (screens, taps, remote presses, the last 10 requests without query strings or bodies, console tail, memory, CPU, thermal and low-power state, app and device). The `replay` module also keeps the last ten seconds of the screen: four small JPEGs a second, longest side 480 px, with the touches in between. After a crash the next launch turns those frames into an MP4 with taps and a countdown drawn on them and uploads it. The dashboard shows it on the crash card and offers it as a download. Recording costs a window snapshot every 250 ms on the main thread and about 120 KB/s of disk writes. Turn it off in Device settings (Crash replay) or leave `replay` out of `debugModules`.

`DebugModule` cases: `performance`, `memory`, `ui`, `swiftui`, `assets`, `accessibility`, `storage`, `feature_flags`, `runtime`, `explorer`, `macro`, `bug`, `push`, `ott`, `crash`, `hang`, `activity`, `playback`, `nav`, `preview`, `replay`.

The Device settings tab in the Debug Suite can override `debugModules`, `captureNetwork`, both body limits and `bodyContentTypes`. Overrides are stored in the `viewlift.capture.settings` defaults suite and applied on top of the configuration passed to `start()`. They can only turn off modules the app enabled, never add new ones. Body limits are capped at 262 144 bytes. Reset deletes the overrides.

The `explorer` module reads live objects through the Objective-C runtime and Swift `Mirror`. Objects are reached through handles issued by the SDK, and handles hold weak references where the class allows it. Writes are type-checked KVC. Calls are limited to zero-argument methods, and selectors such as `init*`, `set*`, `copy*`, `dealloc` and `removeFromSuperview` are refused. Objective-C exceptions are caught by the small `ViewliftCaptureObjC` target and reported as errors.

Redaction defaults are minimal (`cookie`, `set-cookie`, and JSON keys `password` and `secret`). Add `authorization` and your token keys to `extraRedactionKeys`.

## Optional hooks

All of these exist only in debug builds of the package, so wrap calls in `#if DEBUG`.

Structured logs, linked to flows by `requestId`:

```swift
LogService.shared.log(level: .info, subsystem: "Playback", message: "Manifest loaded", requestId: requestId)
```

SwiftUI render counts:

```swift
PlayerView().viewliftRenderProbe("PlayerView")
```

Memory leak tracking. View controllers are tracked automatically from `viewDidLoad`, with the stack that created them, and are reported as leaks when they are still alive 3 seconds after they are dismissed or popped. Register other objects so they show in the live feed and the memory graph, and ask for a leak check when an object should be gone:

```swift
MemoryDebugService.shared.track(playerViewModel, context: "player")
MemoryDebugService.shared.expectRelease(of: playerViewModel, after: 3, context: "player closed")
```

Live tracking streams footprint and object loads and releases to the Debug Suite Memory tab once a second. It also keeps a rolling low-resolution screen recording of about 12 seconds with touch markers, which is attached to each leak report as a replay. It is started from the Memory tab, or in code, and the choice persists across launches:

```swift
MemoryDebugService.shared.startLiveTracking()
```

Each leak report contains the class, its creation stack, the screen it was created on, the objects that still reference it, retain cycle candidates found through Swift reflection and Objective-C ivars, likely causes (closures, timers, observers, non-weak delegates, a view controller still attached to a parent), the taps and scrolls before the leak, and console lines. `memory.heap` walks the malloc heap and counts live instances of every app class, with growth since the previous snapshot. Reflection cannot tell weak references from strong ones, so cycles are candidates to confirm in the Xcode memory graph.

Feature flags that the Debug Suite can toggle:

```swift
DebugFeatureFlags.shared.register(DebugFeatureFlag(key: "new_player", title: "New player", defaultValue: false))
let enabled = DebugFeatureFlags.shared.isEnabled("new_player")
```

App-specific OTT state for the `ott.snapshot` command:

```swift
final class MyOTTSnapshot: OTTDebugSnapshotProviding {
    func ottDebugSnapshot() -> [String: ViewliftNetworkCapture.JSONValue] {
        ["user": .object(["signed_in": .bool(true)])]
    }
}
OTTDebugRegistry.register(myOTTSnapshot)
```

The registry holds the provider weakly, so keep a strong reference. Never put token values or other secrets in the snapshot.

Current screen and playback for the Debug Suite Live tab. The tab also takes a screenshot and tails this app's logs. With no provider, it still reports the top screen and any `AVPlayerLayer` (playing, paused, or buffering; live when the item duration is indefinite). Protected video frames stay black in the screenshot. The probe never reads the asset URL.

```swift
final class MyActivity: ActivityDebugProviding {
    func activityDebugSnapshot() -> [String: ViewliftNetworkCapture.JSONValue] {
        ["title": .string("Evening news"), "content_id": .string("item-1"), "is_live": .bool(true)]
    }
}
ActivityDebugRegistry.register(myActivity)
```

The registry holds the provider weakly. `title`, `content_id`, `state`, `is_live`, `position_seconds`, and `rate` override the player probe. Other keys, including stream URLs and tokens, are dropped.

Playback health for the OTT tab. If you do not attach a player, the SDK follows the `AVPlayer` already on screen. `content_id` from the activity provider is copied onto later events. Do not pass stream URLs.

```swift
PlaybackDebugService.shared.attach(player, contentId: contentId, playbackId: playbackId)
PlaybackDebugService.shared.noteAdBreak(started: true, identifier: adId)
PlaybackDebugService.shared.noteDRM(
    PlaybackDRMNote(phase: .requestStarted, persistent: false)
)
```

FairPlay stays in the host. Observe `AVContentKeySession` there and forward timing with `noteDRM` or `PlaybackDRMProviding`. Never pass SPC, CKC, or key bytes. `playbackDRMDrain` must return only new notes and must not call back into `PlaybackDebugService`.

`playback.waterfall` records playlist, manifest, init, and segment requests (host, status, bytes, TTFB, duration, cache headers). Query strings and bodies are dropped.

`ui.focus` returns the focused view id used by `ui.highlight`, plus the last focus rejection. On Apple TV, Menu, Play/Pause, and Select are posted as `ui` / `remote` events.

`nav.open_url` opens `http`, `https`, or an app scheme on the paired device. `javascript`, `data`, and `file` are rejected. The result omits the query string.

## UI automation

With the `ui` module on, agents can read and drive the app by meaning through the MCP `ui` workflow (`screen_map`, `tap`, `type_text`, `tv_remote`, and the rest). No code is needed in the app. It works on simulators and on real iPhones, iPads, and Apple TVs.

- Elements come from the accessibility tree. SwiftUI views publish their own elements. Stock UIKit views only fill in accessibility values when VoiceOver or a UI-test host is running, so the SDK also describes buttons, labels, text fields, switches, sliders, segmented controls, and cells from their UIKit state.
- Give important controls an `accessibilityIdentifier`. Identifiers match exactly. Labels match without regard to case.
- A tap runs the element's action: the control event (`primaryActionTriggered` or `touchUpInside`), a table or collection view selection, a switch toggle, or `accessibilityActivate()`. Gesture recognizers cannot be triggered from code. For views that only have one, tap on a simulator without `client_id`.
- Swipes and scrolls move the nearest scroll view by most of a page, so lazy lists load the next rows.
- `type_text` inserts text through the focused input's `UIKeyInput`, so delegates and SwiftUI bindings update. Secure field values are never listed.
- On Apple TV, `ui.remote` moves focus to the nearest focusable item in the pressed direction, `select` activates it, `menu` dismisses or pops one screen, and `playpause` toggles the player attached to `PlaybackDebugService`.
- `net.profile` shapes captured traffic inside the app: `wifi`, `4g`, `3g`, `edge`, `gprs`, `offline`, or `off`. It applies only while network capture is on. `latency_ms` and `loss_percent` override a preset.
- Tree nodes carry `window_id`, `scene_id`, and a screen-space frame. `ui.screenshot` and `ui.find` accept `window_id` and `scene_id`. Preview size comes from the active scene.
- Registration includes `device_model`, `os_version`, and `device_hint` (a hash of model, OS, and boot time). Apps from different vendors on one phone share that hint until the device reboots. Simulators leave it empty and share `SIMULATOR_UDID`.
- tvOS sends the background lifecycle report (a short background task) and reports `UIPageControl` pages. Local push on tvOS is badge-only.

## Previews

Register SwiftUI views so agents can render them on the device and compare them with baselines (the MCP `previews` workflow). Register in Debug code only, after `start()`:

```swift
#if DEBUG
ViewliftPreviews.register("Login", states: ["empty", "error"]) { state in
    LoginView(viewModel: .preview(state))
}
ViewliftPreviews.register("Settings") { SettingsView() }
#endif
```

`preview.render` draws one state in an off-screen window at the requested size, scale, and color scheme, so text, fonts, and UIKit-backed controls render as they do in the app. The PNG must fit in 480 KB; larger renders are retried at a lower scale. Build the view from fixed preview data. Never use a signed-in session or real account data.

## Build configurations

The package compiles its capture and debug code only under `#if DEBUG || VIEWLIFT_CAPTURE_ENABLED`. In other builds, `CaptureService.start` does nothing.

Swift Package Manager builds package targets with `DEBUG` in the Debug configuration, so Debug builds work. Adding `VIEWLIFT_CAPTURE_ENABLED` to the **app target's** Active Compilation Conditions does not reach the package, because the package's `Package.swift` defines no `swiftSettings`. A Release or TestFlight build therefore stays a no-op even with that flag. Never ship capture in an App Store build.

## What is captured

Captured: HTTP and HTTPS requests from `URLSession.shared` and from sessions using `URLSessionConfiguration.default` or `.ephemeral` created after `start`.

Not captured:

- WebSocket frames, `WKWebView`, Network.framework, third-party socket stacks;
- background `URLSession` configurations;
- sessions created before `start`, or whose `protocolClasses` are replaced after creation.

Side effects of interception:

- the app's own `URLSessionDelegate` challenge and redirect handling is bypassed;
- responses are buffered, so streaming and progress callbacks do not behave as usual;
- an `X-Viewlift-Capture-Internal: 1` header is added to requests;
- upload tasks and `httpBodyStream` bodies larger than 2 MB are recorded as empty. Smaller stream bodies, including GraphQL JSON that URL loading moves off `httpBody`, are buffered and replayed.

SDK mode is inspection-only. Use the proxy path for breakpoints, mapping, rewrites or throttling.

## Troubleshooting

| Symptom | Check |
|---|---|
| App never appears in the console | SDK receiver enabled in Setup? Both Info.plist keys present? Local network permission granted on the device (Settings, Privacy, Local Network)? |
| Pinning failures in the Xcode console | Fingerprint must match the one shown in Setup. With discovery, leave it empty. |
| Simulator cannot reach the receiver | Enable the receiver on `127.0.0.1` or the Mac's LAN IP. The default port is 8743. |
| Some requests missing | The session was created before `start`, uses a background configuration, or is not `URLSession`. |
| Logs missing at launch | `LogService` sends logs directly and drops them until pairing finishes. |
| Debug Suite says module unavailable | Add the module to `debugModules`. |
| Push command fails on tvOS | Push is iOS-only. On iOS, the app must already have notification permission. |

## Key files

- [ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture/CaptureService.swift](../ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture/CaptureService.swift)
- [ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture/CaptureConfiguration.swift](../ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture/CaptureConfiguration.swift)
- [ios/ViewliftNetworkCapture/README.md](../ios/ViewliftNetworkCapture/README.md)

Next: [iOS SDK internals](ios-sdk-internals.md)
