"""New SwiftUI app projects (iOS, tvOS, macOS) with a file-system-synchronized Xcode project."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host.runner import HostError, check_bundle_id

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_VERSION_RE = re.compile(r"^\d{1,2}\.\d$")

_PLATFORMS: dict[str, dict[str, str]] = {
    "ios": {
        "sdk": "iphoneos",
        "deployment_key": "IPHONEOS_DEPLOYMENT_TARGET",
        "deployment": "17.0",
        "target_settings": (
            "INFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;\n"
            "\t\t\t\tINFOPLIST_KEY_UILaunchScreen_Generation = YES;\n"
            '\t\t\t\tLD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks");\n'
            '\t\t\t\tTARGETED_DEVICE_FAMILY = "1,2";'
        ),
    },
    "tvos": {
        "sdk": "appletvos",
        "deployment_key": "TVOS_DEPLOYMENT_TARGET",
        "deployment": "17.0",
        "target_settings": (
            "INFOPLIST_KEY_UILaunchScreen_Generation = YES;\n"
            '\t\t\t\tLD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks");\n'
            "\t\t\t\tTARGETED_DEVICE_FAMILY = 3;"
        ),
    },
    "macos": {
        "sdk": "macosx",
        "deployment_key": "MACOSX_DEPLOYMENT_TARGET",
        "deployment": "14.0",
        "target_settings": (
            "COMBINE_HIDPI_IMAGES = YES;\n"
            '\t\t\t\tLD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/../Frameworks");'
        ),
    },
}

_PBXPROJ = """// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {
	};
	objectVersion = 77;
	objects = {

/* Begin PBXFileReference section */
		AA0000000000000000000001 /* __NAME__.app */ = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = __NAME__.app; sourceTree = BUILT_PRODUCTS_DIR; };
/* End PBXFileReference section */

/* Begin PBXFileSystemSynchronizedRootGroup section */
		AA0000000000000000000002 /* __NAME__ */ = {isa = PBXFileSystemSynchronizedRootGroup; path = __NAME__; sourceTree = "<group>"; };
/* End PBXFileSystemSynchronizedRootGroup section */

/* Begin PBXFrameworksBuildPhase section */
		AA0000000000000000000003 /* Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
/* End PBXFrameworksBuildPhase section */

/* Begin PBXGroup section */
		AA0000000000000000000004 = {isa = PBXGroup; children = (AA0000000000000000000002, AA0000000000000000000005); sourceTree = "<group>"; };
		AA0000000000000000000005 /* Products */ = {isa = PBXGroup; children = (AA0000000000000000000001); name = Products; sourceTree = "<group>"; };
/* End PBXGroup section */

/* Begin PBXNativeTarget section */
		AA0000000000000000000006 /* __NAME__ */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = AA0000000000000000000007;
			buildPhases = (AA0000000000000000000008, AA0000000000000000000003, AA0000000000000000000009);
			buildRules = ();
			dependencies = ();
			fileSystemSynchronizedGroups = (AA0000000000000000000002);
			name = __NAME__;
			productName = __NAME__;
			productReference = AA0000000000000000000001;
			productType = "com.apple.product-type.application";
		};
/* End PBXNativeTarget section */

/* Begin PBXProject section */
		AA000000000000000000000A /* Project object */ = {
			isa = PBXProject;
			attributes = {
				BuildIndependentTargetsInParallel = 1;
				LastSwiftUpdateCheck = 1600;
				LastUpgradeCheck = 1600;
				TargetAttributes = {
					AA0000000000000000000006 = {CreatedOnToolsVersion = 16.0;};
				};
			};
			buildConfigurationList = AA000000000000000000000B;
			developmentRegion = en;
			hasScannedForEncodings = 0;
			knownRegions = (en, Base);
			mainGroup = AA0000000000000000000004;
			minimizedProjectReferenceProxies = 1;
			preferredProjectObjectVersion = 77;
			productRefGroup = AA0000000000000000000005;
			projectDirPath = "";
			projectRoot = "";
			targets = (AA0000000000000000000006);
		};
/* End PBXProject section */

/* Begin PBXResourcesBuildPhase section */
		AA0000000000000000000009 /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
/* End PBXResourcesBuildPhase section */

/* Begin PBXSourcesBuildPhase section */
		AA0000000000000000000008 /* Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
/* End PBXSourcesBuildPhase section */

/* Begin XCBuildConfiguration section */
		AA000000000000000000000C /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				ALWAYS_SEARCH_USER_PATHS = NO;
				CLANG_ENABLE_MODULES = YES;
				DEBUG_INFORMATION_FORMAT = dwarf;
				ENABLE_TESTABILITY = YES;
				ENABLE_USER_SCRIPT_SANDBOXING = YES;
				GCC_OPTIMIZATION_LEVEL = 0;
				__DEPLOYMENT_KEY__ = __DEPLOYMENT__;
				ONLY_ACTIVE_ARCH = YES;
				SDKROOT = __SDK__;
				SWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";
				SWIFT_OPTIMIZATION_LEVEL = "-Onone";
			};
			name = Debug;
		};
		AA000000000000000000000D /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				ALWAYS_SEARCH_USER_PATHS = NO;
				CLANG_ENABLE_MODULES = YES;
				DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym";
				ENABLE_USER_SCRIPT_SANDBOXING = YES;
				__DEPLOYMENT_KEY__ = __DEPLOYMENT__;
				SDKROOT = __SDK__;
				SWIFT_COMPILATION_MODE = wholemodule;
				VALIDATE_PRODUCT = YES;
			};
			name = Release;
		};
		AA000000000000000000000E /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				CODE_SIGN_STYLE = Automatic;
				CURRENT_PROJECT_VERSION = 1;
				ENABLE_PREVIEWS = YES;
				GENERATE_INFOPLIST_FILE = YES;
				__TARGET_SETTINGS__
				MARKETING_VERSION = 1.0;
				PRODUCT_BUNDLE_IDENTIFIER = __BUNDLE__;
				PRODUCT_NAME = "$(TARGET_NAME)";
				SWIFT_EMIT_LOC_STRINGS = YES;
				SWIFT_VERSION = 5.0;
			};
			name = Debug;
		};
		AA000000000000000000000F /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				CODE_SIGN_STYLE = Automatic;
				CURRENT_PROJECT_VERSION = 1;
				ENABLE_PREVIEWS = YES;
				GENERATE_INFOPLIST_FILE = YES;
				__TARGET_SETTINGS__
				MARKETING_VERSION = 1.0;
				PRODUCT_BUNDLE_IDENTIFIER = __BUNDLE__;
				PRODUCT_NAME = "$(TARGET_NAME)";
				SWIFT_EMIT_LOC_STRINGS = YES;
				SWIFT_VERSION = 5.0;
			};
			name = Release;
		};
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
		AA0000000000000000000007 = {isa = XCConfigurationList; buildConfigurations = (AA000000000000000000000E, AA000000000000000000000F); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };
		AA000000000000000000000B = {isa = XCConfigurationList; buildConfigurations = (AA000000000000000000000C, AA000000000000000000000D); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };
/* End XCConfigurationList section */
	};
	rootObject = AA000000000000000000000A;
}
"""

_SCHEME = """<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion = "1600" version = "1.7">
   <BuildAction parallelizeBuildables = "YES" buildImplicitDependencies = "YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting = "YES" buildForRunning = "YES" buildForProfiling = "YES" buildForArchiving = "YES" buildForAnalyzing = "YES">
            <BuildableReference BuildableIdentifier = "primary" BlueprintIdentifier = "AA0000000000000000000006" BuildableName = "__NAME__.app" BlueprintName = "__NAME__" ReferencedContainer = "container:__NAME__.xcodeproj">
            </BuildableReference>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <TestAction buildConfiguration = "Debug" selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv = "YES">
   </TestAction>
   <LaunchAction buildConfiguration = "Debug" selectedDebuggerIdentifier = "Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier = "Xcode.DebuggerFoundation.Launcher.LLDB" launchStyle = "0" useCustomWorkingDirectory = "NO" ignoresPersistentStateOnLaunch = "NO" debugDocumentVersioning = "YES" debugServiceExtension = "internal" allowLocationSimulation = "YES">
      <BuildableProductRunnable runnableDebuggingMode = "0">
         <BuildableReference BuildableIdentifier = "primary" BlueprintIdentifier = "AA0000000000000000000006" BuildableName = "__NAME__.app" BlueprintName = "__NAME__" ReferencedContainer = "container:__NAME__.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </LaunchAction>
   <ArchiveAction buildConfiguration = "Release" revealArchiveInOrganizer = "YES">
   </ArchiveAction>
</Scheme>
"""

_APP_SWIFT = """import SwiftUI

@main
struct __NAME__App: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
"""

_CONTENT_SWIFT = """import SwiftUI

struct ContentView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "sparkles")
                .imageScale(.large)
            Text("Hello, __NAME__")
                .accessibilityIdentifier("greeting")
        }
        .padding()
    }
}

#Preview {
    ContentView()
}
"""


def scaffold(ctx: HostContext, args: dict[str, Any], *, default_platform: str) -> dict[str, Any]:
    name = str(args.get("projectName") or "")
    if not _NAME_RE.match(name):
        raise HostError("projectName must start with a letter and use letters, digits, or _.")
    platform = str(args.get("platform") or default_platform).lower()
    if platform not in _PLATFORMS:
        raise HostError("platform must be iOS, tvOS, or macOS.")
    if default_platform == "macos" and platform != "macos":
        raise HostError("scaffold_macos_project only builds macOS apps.")
    spec = _PLATFORMS[platform]
    output = ctx.roots.resolve(str(args.get("outputPath") or ""))
    if not output.is_dir():
        raise HostError("outputPath must be an existing folder inside a project root.")
    root = output / name
    if root.exists():
        raise HostError(f"{root} already exists.")
    bundle = check_bundle_id(str(args.get("bundleIdentifier") or f"com.example.{name.lower()}"))
    deployment = str(args.get("deploymentTarget") or spec["deployment"])
    if not _VERSION_RE.match(deployment):
        raise HostError("deploymentTarget looks like 17.0.")

    def fill(text: str) -> str:
        return (
            text.replace("__TARGET_SETTINGS__", spec["target_settings"])
            .replace("__DEPLOYMENT_KEY__", spec["deployment_key"])
            .replace("__DEPLOYMENT__", deployment)
            .replace("__SDK__", spec["sdk"])
            .replace("__BUNDLE__", bundle)
            .replace("__NAME__", name)
        )

    files = {
        Path(f"{name}.xcodeproj/project.pbxproj"): fill(_PBXPROJ),
        Path(f"{name}.xcodeproj/xcshareddata/xcschemes/{name}.xcscheme"): fill(_SCHEME),
        Path(f"{name}/{name}App.swift"): fill(_APP_SWIFT),
        Path(f"{name}/ContentView.swift"): fill(_CONTENT_SWIFT),
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {
        "projectPath": str(root / f"{name}.xcodeproj"),
        "scheme": name,
        "bundleId": bundle,
        "platform": platform,
        "files": [str(root / item) for item in files],
        "next": "Call build_run_sim (or build_run_macos) with this projectPath and scheme.",
    }


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add(
        "scaffold_ios_project",
        lambda args: scaffold(ctx, args, default_platform="ios"),
        mutating=True,
    )
    toolkit.add(
        "scaffold_macos_project",
        lambda args: scaffold(ctx, args, default_platform="macos"),
        mutating=True,
    )
