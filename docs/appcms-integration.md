# AppCMS integration

This page summarizes how `ViewliftNetworkCapture` is wired into the AppCMS (ViewliftOne) iOS and tvOS apps. The full, step-by-step procedure is the Cursor skill [.cursor/skills/reapply-viewlift-network-capture/SKILL.md](../.cursor/skills/reapply-viewlift-network-capture/SKILL.md); follow it when applying the changes.

## Fast path

The integration already exists on branch `Develop_ViewliftNetworkCapture` in `snagfilms/iOS-ViewliftONE`. Check it out or cherry-pick its capture commit, resolve packages, and build both targets in Debug.

## What the integration contains

1. **Swift package** on both **AppCMS** and **AppCMS_tvOS**: one remote reference to `https://github.com/rakesh-vl/viewlift-ai.git` (branch `main`) and the `ViewliftNetworkCapture` product linked under Frameworks for each target. Do not use a local relative path on shared branches.
2. **Two host files** in `AppCMS/AppCMS/AppDelegate/`, added to both targets, both wrapped in `#if DEBUG`:
   - [ViewliftCaptureDebugService.swift](../.cursor/skills/reapply-viewlift-network-capture/references/ViewliftCaptureDebugService.swift) builds the configuration (discovery placeholder by default, all debug modules), registers the OTT snapshot provider and starts capture.
   - [AppCMSOTTDebugSnapshot.swift](../.cursor/skills/reapply-viewlift-network-capture/references/AppCMSOTTDebugSnapshot.swift) implements `OTTDebugSnapshotProviding` with user, subscription, entitlement, player, config, navigation and page state. Tokens are reported only as present or absent with a length; email is masked.
3. **Start call** on the first lines of `application(_:didFinishLaunchingWithOptions:)` in both the iOS and tvOS `AppDelegate.swift`:

   ```swift
   #if DEBUG
   ViewliftCaptureDebugService.shared.start()
   #endif
   ```

4. **Info.plist** for both targets: `NSBonjourServices` with `_viewliftdebug._tcp`, and `NSLocalNetworkUsageDescription` mentioning Viewlift debug capture.

## Optional scheme overrides

`ViewliftCaptureDebugService` reads these from the scheme's environment. Without them, Bonjour discovery is used.

| Variable | Default |
|---|---|
| `VIEWLIFT_CAPTURE_HOST` | `0.0.0.0` |
| `VIEWLIFT_CAPTURE_PORT` | `9` |
| `VIEWLIFT_CAPTURE_FINGERPRINT` | empty |
| `VIEWLIFT_CAPTURE_DEBUG_KEY` | empty |

## Notes for AppCMS code

- ViewliftOne defines `JSONValue` as a SwiftyJSON alias. The OTT snapshot therefore uses `private typealias CaptureJSON = ViewliftNetworkCapture.JSONValue`. Keep that qualification.
- For new host-side JSON or dictionary parsing, use SwiftyJSON from `ViewliftOne/Core/Utils`, as elsewhere in AppCMS.
- Keep the bridge in a service class, following the app's MVVM-plus-service pattern; view models should not call the SDK.
- Proxyman and Atlantis are not needed for this path.

## Verify

- Build **AppCMS** and **AppCMS_tvOS** in Debug with no errors or SwiftLint violations.
- Confirm Release builds do not call `CaptureService.shared.start`.
- Run the app, then check that it appears in the console's app picker and its flows show source `SDK`.

Next: [Contributing](contributing.md)
