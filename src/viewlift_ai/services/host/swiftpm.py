"""Swift Package Manager build, test, run, and clean."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai import defaults
from viewlift_ai.services.host.jobs import JobContext, condense_log
from viewlift_ai.services.host.runner import HostError, check_launch_args, check_name

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

MAX_WAIT = defaults.JOB_WAIT_SECONDS
_RUNNING: dict[str, tuple[subprocess.Popen[str], str]] = {}
_LOCK = threading.Lock()


def package_path(ctx: HostContext, args: dict[str, Any]) -> Path:
    raw = str(ctx.defaults.merged(args).get("packagePath") or "")
    path = ctx.roots.resolve(raw)
    if path.is_file() and path.name == "Package.swift":
        path = path.parent
    if not (path / "Package.swift").is_file():
        raise HostError("packagePath must be a folder that contains Package.swift.")
    return path


def _configuration(args: dict[str, Any]) -> list[str]:
    value = str(args.get("configuration") or "debug").lower()
    if value not in {"debug", "release"}:
        raise HostError("configuration must be debug or release.")
    return ["-c", value]


def _optional(flag: str, value: Any, label: str) -> list[str]:
    return [flag, check_name(str(value), label)] if value else []


def _job(
    ctx: HostContext, kind: str, title: str, argv: list[str], cwd: Path, wait: float
) -> dict[str, Any]:
    def body(job: JobContext) -> dict[str, Any]:
        code, output = job.run(argv, cwd=cwd)
        return {"ok": code == 0, "exit_code": code, "summary": condense_log(output)}

    job = ctx.jobs.start(kind, title, body)
    job.done.wait(max(0.0, min(wait, MAX_WAIT)))
    out = ctx.jobs.describe(job.id)
    if job.state == "running":
        out["next"] = "Call wait_job with this job_id until state is not running."
    return out


def build(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = package_path(ctx, args)
    argv = [
        "swift",
        "build",
        "--package-path",
        str(path),
        *_configuration(args),
        *_optional("--target", args.get("targetName"), "targetName"),
        *_optional("--product", args.get("product"), "product"),
    ]
    return _job(
        ctx,
        "swift_build",
        f"swift build {path.name}",
        argv,
        path,
        float(args.get("waitSeconds", 20)),
    )


def test(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = package_path(ctx, args)
    argv = ["swift", "test", "--package-path", str(path), *_configuration(args)]
    if args.get("filter"):
        text = str(args["filter"])
        if len(text) > 200 or "\n" in text:
            raise HostError("filter is too long.")
        argv += ["--filter", text]
    if args.get("parallel"):
        argv.append("--parallel")
    if args.get("coverage"):
        argv.append("--enable-code-coverage")
    return _job(
        ctx, "swift_test", f"swift test {path.name}", argv, path, float(args.get("waitSeconds", 20))
    )


def run(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = package_path(ctx, args)
    executable = args.get("executableName")
    argv = [
        "swift",
        "run",
        "--package-path",
        str(path),
        *_configuration(args),
        *([check_name(str(executable), "executableName")] if executable else []),
        *check_launch_args(args.get("arguments")),
    ]
    holder: dict[str, Any] = {}

    def body(job: JobContext) -> dict[str, Any]:
        process = job.spawn(argv, cwd=path)
        holder["proc"] = process
        code = process.wait()
        with _LOCK:
            _RUNNING.pop(job.job.id, None)
        return {"ok": code == 0, "exit_code": code}

    job = ctx.jobs.start("swift_run", f"swift run {path.name}", body)
    for _ in range(100):
        if "proc" in holder:
            break
        job.done.wait(0.05)
    if "proc" in holder:
        with _LOCK:
            _RUNNING[job.id] = (holder["proc"], str(path))
    job.done.wait(max(0.0, min(float(args.get("waitSeconds", 3)), MAX_WAIT)))
    return ctx.jobs.describe(job.id, log_tail=60)


def stop(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    job_id = str(args.get("job_id") or "")
    with _LOCK:
        ids = [job_id] if job_id else list(_RUNNING)
    stopped = []
    for item in ids:
        if item in _RUNNING:
            ctx.jobs.cancel(item)
            stopped.append(item)
    if job_id and not stopped:
        raise HostError("That job is not a running swift_package_run.")
    return {"stopped": stopped}


def list_packages(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        running = [{"job_id": key, "packagePath": value[1]} for key, value in _RUNNING.items()]
    out: dict[str, Any] = {"running": running}
    if ctx.defaults.merged(args).get("packagePath"):
        path = package_path(ctx, args)
        data = ctx.run_json(
            ["swift", "package", "--package-path", str(path), "dump-package"], timeout=120
        )
        out["package"] = {
            "name": data.get("name"),
            "products": [
                {"name": item.get("name"), "type": next(iter(item.get("type") or {}), "")}
                for item in data.get("products", [])
            ],
            "targets": [
                {"name": item.get("name"), "type": item.get("type")}
                for item in data.get("targets", [])
            ],
            "dependencies": [
                (item.get("sourceControl") or [{}])[0].get("identity")
                or next(iter((item.get("fileSystem") or [{}])[0].values()), "")
                for item in data.get("dependencies", [])
                if isinstance(item, dict)
            ],
        }
    return out


def clean(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = package_path(ctx, args)
    result = ctx.run(["swift", "package", "--package-path", str(path), "clean"], timeout=120)
    return {"ok": result.ok, "packagePath": str(path), "stderr": result.stderr[-1000:]}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "swift_package_build": (build, True),
        "swift_package_test": (test, True),
        "swift_package_run": (run, True),
        "swift_package_stop": (stop, True),
        "swift_package_list": (list_packages, False),
        "swift_package_clean": (clean, True),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
