---
name: reapply-viewlift-network-capture
description: >-
  Reapplies ViewliftNetworkCapture debug SDK wiring to AppCMS iOS and tvOS
  targets via git SPM from viewlift-ai. Use when enabling Viewlift AI network
  capture on debug builds, restoring Develop_ViewliftNetworkCapture integration,
  adding ViewliftNetworkCapture SPM to AppCMS/AppCMS_tvOS, or wiring
  ViewliftCaptureDebugService / AppCMSOTTDebugSnapshot.
---

# Reapply ViewliftNetworkCapture (debug builds)

Wire the Viewlift AI SDK capture path into AppCMS **DEBUG** builds only. Prefer
branch `Develop_ViewliftNetworkCapture` on `iOS-ViewliftONE` when available;
otherwise reapply the steps below onto the current AppCMS checkout.

## Preconditions

- AppCMS repo (typical local path):
  `/Users/nexg/Documents/Viewlift/Main_Projects/Clone_2/Untitled`
- Viewlift AI package repo: `https://github.com/rakesh-vl/viewlift-ai.git`
  - Root `Package.swift` exposes product `ViewliftNetworkCapture`
  - Sources live under `ios/ViewliftNetworkCapture/`
- Do **not** add capture start to Release / App Store configs

## Fast path

If remote branch exists:

```bash
cd /Users/nexg/Documents/Viewlift/Main_Projects/Clone_2/Untitled
git fetch origin Develop_ViewliftNetworkCapture
git checkout Develop_ViewliftNetworkCapture
# or: git cherry-pick <capture-commit> onto your debug branch
```

Then resolve packages in Xcode and build `AppCMS` + `AppCMS_tvOS` DEBUG.

## Manual reapply checklist

Copy and track:

```
Progress:
- [ ] 1. Add git SPM ViewliftNetworkCapture to AppCMS + AppCMS_tvOS
- [ ] 2. Copy DEBUG host files into AppCMS/AppCMS/AppDelegate/
- [ ] 3. Register files in project.pbxproj (both target Sources + Frameworks)
- [ ] 4. Start capture in both AppDelegates under #if DEBUG
- [ ] 5. Info.plist Bonjour + NSLocalNetworkUsageDescription
- [ ] 6. Build iOS and tvOS DEBUG; confirm no Release linkage of start()
```

### 1. Git SPM (both targets)

Xcode → AppCMS project → Package Dependencies → Add Package:

- URL: `https://github.com/rakesh-vl/viewlift-ai.git`
- Rule: branch `main` (or pinned revision after publish)
- Product: `ViewliftNetworkCapture`
- Add to targets: **AppCMS** and **AppCMS_tvOS**

`project.pbxproj` shape (IDs may differ; keep one remote ref, two product deps):

```
XCRemoteSwiftPackageReference "viewlift-ai"
  repositoryURL = "https://github.com/rakesh-vl/viewlift-ai.git"
  requirement.kind = branch
  requirement.branch = main

XCSwiftPackageProductDependency ViewliftNetworkCapture
  package = <viewlift-ai ref>
  productName = ViewliftNetworkCapture
```

Link `ViewliftNetworkCapture in Frameworks` on both app targets.
Do **not** use the local path
`../../../../../Viewlift_AI/ios/ViewliftNetworkCapture` for shared debug
branches; local path is only for package iteration inside Viewlift_AI.

### 2. Host files

Copy from this skill’s references into
`AppCMS/AppCMS/AppDelegate/` (shared by iOS + tvOS Sources):

- [references/ViewliftCaptureDebugService.swift](references/ViewliftCaptureDebugService.swift)
- [references/AppCMSOTTDebugSnapshot.swift](references/AppCMSOTTDebugSnapshot.swift)

Both files are wrapped in `#if DEBUG` so Release compiles them out.

### 3. AppDelegate start (before app networking)

**iOS** `AppCMS/AppCMS/AppDelegate/AppDelegate.swift` — first lines of
`application(_:didFinishLaunchingWithOptions:)`:

```swift
#if DEBUG
        ViewliftCaptureDebugService.shared.start()
#endif
```

**tvOS** `AppCMS/AppCMS_tvOS/AppDelegate/AppDelegate.swift` — same block at
the top of `didFinishLaunchingWithOptions`.

No Atlantis / Proxyman required for ViewliftNetworkCapture.

### 4. Info.plist

**iOS** `AppCMS/TargetSpecificResources/iOS/Info.plist`:

- Add Bonjour service `_viewliftdebug._tcp` under `NSBonjourServices`
- Extend `NSLocalNetworkUsageDescription` to mention Viewlift debug capture

**tvOS** `AppCMS/TargetSpecificResources/tvOS/Info.plist`:

- Add `NSLocalNetworkUsageDescription` (Viewlift debug capture text)
- Add `NSBonjourServices` with `_viewliftdebug._tcp`

### 5. Runtime config

Optional scheme environment overrides (else discovery defaults apply):

- `VIEWLIFT_CAPTURE_HOST`
- `VIEWLIFT_CAPTURE_PORT`
- `VIEWLIFT_CAPTURE_FINGERPRINT`
- `VIEWLIFT_CAPTURE_DEBUG_KEY`

### 6. Verify

- Build **AppCMS** DEBUG and **AppCMS_tvOS** DEBUG
- Confirm Release does not call `CaptureService.shared.start`
- Dashboard Setup page: device appears; HTTP flows labeled SDK

## Boundaries

- Scope = ViewliftNetworkCapture only (not Braze probes, Atlantis, analytics WIP)
- Keep AppCMS MVVM/service patterns; host bridge stays DEBUG-only
- Prefer SwiftyJSON from ViewliftOne/Core/Utils when parsing host JSON in new code;
  `AppCMSOTTDebugSnapshot` uses `ViewliftNetworkCapture.JSONValue` intentionally
- After any one-off repair script/log: delete it

## Reference branch

- Repo: `snagfilms/iOS-ViewliftONE`
- Branch: `Develop_ViewliftNetworkCapture`
- Package: `rakesh-vl/viewlift-ai` product `ViewliftNetworkCapture`
