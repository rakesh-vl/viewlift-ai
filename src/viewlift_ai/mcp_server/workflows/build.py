"""Session defaults, project discovery, Xcode builds (simulator, device, macOS), SwiftPM, coverage,
scaffolding, and background jobs."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai import defaults
from viewlift_ai.mcp_server.workflows.common import DESTRUCTIVE, READ, WRITE, RunFn, host


def register(mcp: FastMCP, run: RunFn) -> None:  # noqa: C901 - one block of tool definitions
    def call(
        name: str, args: dict[str, Any], *, mutating: bool = False, confirm: bool = False
    ) -> Any:
        logged = {**args, "confirm": confirm} if mutating else args
        return run(name, logged, lambda: host(name, args, mutating=mutating, confirm=confirm))

    # Session defaults and environment -------------------------------------------------------

    @mcp.tool(annotations=READ)
    def doctor() -> dict[str, Any]:
        """Check Xcode, simctl, devicectl, lldb, xccov, AXe or idb, project roots, and defaults."""
        return call("doctor", {})

    @mcp.tool(annotations=READ)
    def session_show_defaults() -> dict[str, Any]:
        """Show the active defaults profile (project, scheme, simulator, device, bundle id)."""
        return call("session_show_defaults", {})

    @mcp.tool(annotations=WRITE)
    def session_set_defaults(
        projectPath: str = "",
        workspacePath: str = "",
        packagePath: str = "",
        scheme: str = "",
        configuration: str = "",
        platform: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        bundleId: str = "",
        derivedDataPath: str = "",
        developmentTeam: str = "",
        profile: str = "",
    ) -> dict[str, Any]:
        """Save defaults so later build, run, and UI calls can omit them. Local config only.

        developmentTeam is the Apple team id used to sign the UI runner for devices.
        """
        args = {
            "projectPath": projectPath,
            "workspacePath": workspacePath,
            "packagePath": packagePath,
            "scheme": scheme,
            "configuration": configuration,
            "platform": platform,
            "simulatorId": simulatorId,
            "simulatorName": simulatorName,
            "deviceId": deviceId,
            "bundleId": bundleId,
            "derivedDataPath": derivedDataPath,
            "developmentTeam": developmentTeam,
            "profile": profile,
        }
        return call("session_set_defaults", {k: v for k, v in args.items() if v})

    @mcp.tool(annotations=WRITE)
    def session_clear_defaults(keys: list[str] | None = None, all: bool = False) -> dict[str, Any]:  # noqa: A002
        """Clear some keys, the active profile, or (all=true) every profile."""
        return call("session_clear_defaults", {"keys": keys or [], "all": all})

    @mcp.tool(annotations=WRITE)
    def session_use_defaults_profile(profile: str, create: bool = False) -> dict[str, Any]:
        """Switch to a named defaults profile, for example one per app or per platform."""
        return call("session_use_defaults_profile", {"profile": profile, "create": create})

    @mcp.tool(annotations=WRITE)
    def sync_xcode_defaults() -> dict[str, Any]:
        """Set projectPath or workspacePath (and a scheme) from the project Xcode built last."""
        return call("sync_xcode_defaults", {})

    # Jobs -----------------------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def get_job(job_id: str, log_tail: int = 0) -> dict[str, Any]:
        """State and condensed result of one build, test, run, or recording job."""
        return call("get_job", {"job_id": job_id, "log_tail": log_tail})

    @mcp.tool(annotations=READ)
    def wait_job(job_id: str, timeout_s: float = defaults.JOB_WAIT_SECONDS) -> dict[str, Any]:
        """Block up to 30 s for a job to finish, then return its state and summary."""
        return call("wait_job", {"job_id": job_id, "timeout_s": timeout_s})

    @mcp.tool(annotations=WRITE)
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Stop a running job (sends SIGINT to its process)."""
        return call("cancel_job", {"job_id": job_id})

    @mcp.tool(annotations=READ)
    def list_jobs() -> dict[str, Any]:
        """Recent jobs, newest first."""
        return call("list_jobs", {})

    # Project ----------------------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def discover_projs(workspaceRoot: str = "", maxDepth: int = 5) -> dict[str, Any]:
        """Find .xcodeproj, .xcworkspace, and Package.swift inside the project roots."""
        return call("discover_projs", {"workspaceRoot": workspaceRoot, "maxDepth": maxDepth})

    @mcp.tool(annotations=READ)
    def list_schemes(projectPath: str = "", workspacePath: str = "") -> dict[str, Any]:
        """Schemes, targets, and configurations of a project or workspace."""
        return call("list_schemes", {"projectPath": projectPath, "workspacePath": workspacePath})

    @mcp.tool(annotations=READ)
    def show_build_settings(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        keys: list[str] | None = None,
        all: bool = False,  # noqa: A002
    ) -> dict[str, Any]:
        """Key build settings per target (bundle id, SDK, deployment targets). all=true for all."""
        args = {
            "scheme": scheme,
            "projectPath": projectPath,
            "workspacePath": workspacePath,
            "configuration": configuration,
            "keys": keys or [],
            "all": all,
        }
        return call("show_build_settings", args)

    @mcp.tool(annotations=READ)
    def get_app_bundle_id(appPath: str) -> dict[str, Any]:
        """Bundle id, version, and executable from a built iOS or tvOS .app."""
        return call("get_app_bundle_id", {"appPath": appPath})

    @mcp.tool(annotations=READ)
    def get_mac_bundle_id(appPath: str) -> dict[str, Any]:
        """Bundle id, version, and executable from a built macOS .app."""
        return call("get_mac_bundle_id", {"appPath": appPath})

    @mcp.tool(annotations=DESTRUCTIVE)
    def clean(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """xcodebuild clean for one scheme. Deletes build products. confirm must be true."""
        args = {
            "scheme": scheme,
            "projectPath": projectPath,
            "workspacePath": workspacePath,
            "configuration": configuration,
        }
        return call("clean", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def scaffold_ios_project(
        projectName: str,
        outputPath: str,
        bundleIdentifier: str = "",
        platform: str = "iOS",
        deploymentTarget: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a SwiftUI iOS or tvOS app project inside a project root. confirm=true."""
        args = {
            "projectName": projectName,
            "outputPath": outputPath,
            "bundleIdentifier": bundleIdentifier,
            "platform": platform,
            "deploymentTarget": deploymentTarget,
        }
        return call("scaffold_ios_project", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def scaffold_macos_project(
        projectName: str,
        outputPath: str,
        bundleIdentifier: str = "",
        deploymentTarget: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a SwiftUI macOS app project inside a project root. confirm must be true."""
        args = {
            "projectName": projectName,
            "outputPath": outputPath,
            "bundleIdentifier": bundleIdentifier,
            "deploymentTarget": deploymentTarget,
        }
        return call("scaffold_macos_project", args, mutating=True, confirm=confirm)

    # Simulator build, run, test ---------------------------------------------------------------

    def build_args(
        scheme: str,
        projectPath: str,
        workspacePath: str,
        configuration: str,
        extraArgs: list[str] | None,
        waitSeconds: float,
        **more: Any,
    ) -> dict[str, Any]:
        return {
            "scheme": scheme,
            "projectPath": projectPath,
            "workspacePath": workspacePath,
            "configuration": configuration,
            "extraArgs": extraArgs or [],
            "waitSeconds": waitSeconds,
            **more,
        }

    @mcp.tool(annotations=WRITE)
    def build_sim(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        simulatorName: str = "",
        simulatorId: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build a scheme for a simulator. Returns a job; errors and warnings come condensed."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            simulatorName=simulatorName,
            simulatorId=simulatorId,
        )
        return call("build_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def build_run_sim(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        simulatorName: str = "",
        simulatorId: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        launchArgs: list[str] | None = None,
        env: dict[str, str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build, install, and launch on a simulator (boots it if needed). Returns a job.

        launchArgs reach ProcessInfo.arguments; extraArgs go to xcodebuild only.
        """
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            simulatorName=simulatorName,
            simulatorId=simulatorId,
            launchArgs=launchArgs or [],
            env=env or {},
        )
        return call("build_run_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def test_sim(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        simulatorName: str = "",
        simulatorId: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        coverage: bool = True,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run tests on a simulator. Use extraArgs ["-only-testing:Target/Case"] to narrow."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            simulatorName=simulatorName,
            simulatorId=simulatorId,
            coverage=coverage,
        )
        return call("test_sim", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def get_sim_app_path(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        simulatorName: str = "",
        simulatorId: str = "",
        configuration: str = "",
    ) -> dict[str, Any]:
        """Path and bundle id of the .app a simulator build produces."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            None,
            0,
            simulatorName=simulatorName,
            simulatorId=simulatorId,
        )
        return call("get_sim_app_path", args)

    # Physical devices ------------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def list_physical_devices() -> dict[str, Any]:
        """Paired iPhone, iPad, and Apple TV hardware from devicectl (not app device_keys)."""
        return call("list_physical_devices", {})

    @mcp.tool(annotations=WRITE)
    def build_device(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        deviceId: str = "",
        platform: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build for a paired device. Needs code signing set up in Xcode. Returns a job."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            deviceId=deviceId,
            platform=platform,
        )
        return call("build_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def build_run_device(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        deviceId: str = "",
        platform: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        launchArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build, install, and launch on a paired device. Returns a job."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            deviceId=deviceId,
            platform=platform,
            launchArgs=launchArgs or [],
        )
        return call("build_run_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def test_device(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        deviceId: str = "",
        platform: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run tests on a paired device. Returns a job and the .xcresult path."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            deviceId=deviceId,
            platform=platform,
        )
        return call("test_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def get_device_app_path(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        deviceId: str = "",
        platform: str = "",
        configuration: str = "",
    ) -> dict[str, Any]:
        """Path and bundle id of the .app a device build produces."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            None,
            0,
            deviceId=deviceId,
            platform=platform,
        )
        return call("get_device_app_path", args)

    @mcp.tool(annotations=WRITE)
    def install_app_device(
        appPath: str, deviceId: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Install a built .app on a paired device. confirm must be true."""
        args = {"appPath": appPath, "deviceId": deviceId}
        return call("install_app_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def launch_app_device(
        bundleId: str = "",
        deviceId: str = "",
        launchArgs: list[str] | None = None,
        env: dict[str, str] | None = None,
        console: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Launch an installed app on a paired device (restarts it if running).

        env sets environment variables (DYLD_ and LD_ are refused). console=true streams the
        app's stdout and stderr into a job log; read it with get_job log_tail.
        """
        args = {
            "bundleId": bundleId,
            "deviceId": deviceId,
            "launchArgs": launchArgs or [],
            "env": env or {},
            "console": console,
        }
        return call("launch_app_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def open_url_device(
        url: str, bundleId: str = "", deviceId: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Open a URL in an app on a paired device (devicectl relaunches it with the URL)."""
        args = {"url": url, "bundleId": bundleId, "deviceId": deviceId}
        return call("open_url_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def list_device_apps(deviceId: str = "", includeSystem: bool = False) -> dict[str, Any]:
        """Apps installed on a paired device with bundle id, name, and version."""
        return call("list_device_apps", {"deviceId": deviceId, "includeSystem": includeSystem})

    @mcp.tool(annotations=READ)
    def list_device_processes(deviceId: str = "") -> dict[str, Any]:
        """Running processes on a paired device with pid and executable."""
        return call("list_device_processes", {"deviceId": deviceId})

    @mcp.tool(annotations=WRITE)
    def copy_from_device(
        source: str, bundleId: str = "", deviceId: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Copy a file or folder from an app's data container into the console work folder.

        source is relative to the container (for example Library/Caches/log.txt). The app
        must be a development build. Returns the local path, not the contents.
        """
        args = {"source": source, "bundleId": bundleId, "deviceId": deviceId}
        return call("copy_from_device", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def device_tunnel_status() -> dict[str, Any]:
        """Whether the iOS 17+ developer tunnel (pymobiledevice3 tunneld) runs, and for what."""
        return call("device_tunnel_status", {})

    @mcp.tool(annotations=WRITE)
    def device_tunnel_start(confirm: bool = False) -> dict[str, Any]:
        """Start the developer tunnel. macOS shows an administrator prompt to the user.

        Needed for location, screenshots, and syslog on iOS, iPadOS, and tvOS 17 or later.
        """
        return call("device_tunnel_start", {}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def device_tunnel_stop(confirm: bool = False) -> dict[str, Any]:
        """Stop the developer tunnel."""
        return call("device_tunnel_stop", {}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def device_pair_browse() -> dict[str, Any]:
        """Apple TVs showing Settings > Remotes and Devices > Remote App and Devices."""
        return call("device_pair_browse", {})

    @mcp.tool(annotations=WRITE)
    def device_pair_start(identifier: str, confirm: bool = False) -> dict[str, Any]:
        """Start pairing with one Apple TV from device_pair_browse. The TV then shows a PIN."""
        return call("device_pair_start", {"identifier": identifier}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def device_pair_pin(pin: str, confirm: bool = False) -> dict[str, Any]:
        """Send the PIN the Apple TV shows. Poll device_pair_status until paired."""
        return run(
            "device_pair_pin",
            {"confirm": confirm},
            lambda: host("device_pair_pin", {"pin": pin}, mutating=True, confirm=confirm),
        )

    @mcp.tool(annotations=READ)
    def device_pair_status() -> dict[str, Any]:
        """State of the current pairing: waiting_for_pin, pairing, paired, or failed."""
        return call("device_pair_status", {})

    @mcp.tool(annotations=READ)
    def list_capture_devices() -> dict[str, Any]:
        """USB iPhones and iPads this Mac can record (record_video uses them)."""
        return call("list_capture_devices", {})

    @mcp.tool(annotations=WRITE)
    def stop_app_device(
        bundleId: str = "", deviceId: str = "", processId: int | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Stop an app on a paired device by processId, or the one launch_app_device started."""
        args = {"bundleId": bundleId, "deviceId": deviceId, "processId": processId}
        return call("stop_app_device", args, mutating=True, confirm=confirm)

    # macOS ----------------------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def build_macos(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        arch: str = "",
        extraArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build a macOS scheme. Returns a job."""
        args = build_args(
            scheme, projectPath, workspacePath, configuration, extraArgs, waitSeconds, arch=arch
        )
        return call("build_macos", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def build_run_macos(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        arch: str = "",
        extraArgs: list[str] | None = None,
        launchArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Build and open a macOS app. Returns a job."""
        args = build_args(
            scheme,
            projectPath,
            workspacePath,
            configuration,
            extraArgs,
            waitSeconds,
            arch=arch,
            launchArgs=launchArgs or [],
        )
        return call("build_run_macos", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def test_macos(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        extraArgs: list[str] | None = None,
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run macOS tests. Returns a job and the .xcresult path."""
        args = build_args(scheme, projectPath, workspacePath, configuration, extraArgs, waitSeconds)
        return call("test_macos", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def get_mac_app_path(
        scheme: str = "",
        projectPath: str = "",
        workspacePath: str = "",
        configuration: str = "",
        arch: str = "",
    ) -> dict[str, Any]:
        """Path and bundle id of the .app a macOS build produces."""
        args = build_args(scheme, projectPath, workspacePath, configuration, None, 0, arch=arch)
        return call("get_mac_app_path", args)

    @mcp.tool(annotations=WRITE)
    def launch_mac_app(
        appPath: str, launchArgs: list[str] | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Open a built macOS .app. confirm must be true."""
        args = {"appPath": appPath, "launchArgs": launchArgs or []}
        return call("launch_mac_app", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def stop_mac_app(
        appPath: str = "", bundleId: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Quit a macOS app by bundle id or .app path. confirm must be true."""
        args = {"appPath": appPath, "bundleId": bundleId}
        return call("stop_mac_app", args, mutating=True, confirm=confirm)

    # SwiftPM ----------------------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def swift_package_build(
        packagePath: str = "",
        targetName: str = "",
        product: str = "",
        configuration: str = "debug",
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """swift build for a package inside the project roots. Returns a job."""
        args = {
            "packagePath": packagePath,
            "targetName": targetName,
            "product": product,
            "configuration": configuration,
            "waitSeconds": waitSeconds,
        }
        return call("swift_package_build", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def swift_package_test(
        packagePath: str = "",
        filter: str = "",  # noqa: A002
        parallel: bool = False,
        coverage: bool = False,
        configuration: str = "debug",
        waitSeconds: float = 20,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """swift test, optionally filtered. Returns a job with test failures condensed."""
        args = {
            "packagePath": packagePath,
            "filter": filter,
            "parallel": parallel,
            "coverage": coverage,
            "configuration": configuration,
            "waitSeconds": waitSeconds,
        }
        return call("swift_package_test", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def swift_package_run(
        packagePath: str = "",
        executableName: str = "",
        arguments: list[str] | None = None,
        configuration: str = "debug",
        waitSeconds: float = 3,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """swift run an executable in the background. Stop it with swift_package_stop."""
        args = {
            "packagePath": packagePath,
            "executableName": executableName,
            "arguments": arguments or [],
            "configuration": configuration,
            "waitSeconds": waitSeconds,
        }
        return call("swift_package_run", args, mutating=True, confirm=confirm)

    @mcp.tool(annotations=WRITE)
    def swift_package_stop(job_id: str = "", confirm: bool = False) -> dict[str, Any]:
        """Stop one swift_package_run job, or all of them."""
        return call("swift_package_stop", {"job_id": job_id}, mutating=True, confirm=confirm)

    @mcp.tool(annotations=READ)
    def swift_package_list(packagePath: str = "") -> dict[str, Any]:
        """Running swift_package_run jobs, plus products and targets when packagePath is set."""
        return call("swift_package_list", {"packagePath": packagePath})

    @mcp.tool(annotations=DESTRUCTIVE)
    def swift_package_clean(packagePath: str = "", confirm: bool = False) -> dict[str, Any]:
        """swift package clean. Deletes .build products. confirm must be true."""
        return call(
            "swift_package_clean", {"packagePath": packagePath}, mutating=True, confirm=confirm
        )

    # Coverage -------------------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def get_coverage_report(
        xcresultPath: str = "", target: str = "", showFiles: bool = False
    ) -> dict[str, Any]:
        """Line coverage per target from an .xcresult (default: the latest test job)."""
        args = {"xcresultPath": xcresultPath, "target": target, "showFiles": showFiles}
        return call("get_coverage_report", args)

    @mcp.tool(annotations=READ)
    def get_file_coverage(file: str, xcresultPath: str = "") -> dict[str, Any]:
        """Coverage for one source file with uncovered line ranges."""
        return call("get_file_coverage", {"file": file, "xcresultPath": xcresultPath})
