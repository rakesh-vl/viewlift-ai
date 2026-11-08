"""Background jobs for long host work (builds, tests, recordings) with a log file per job."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from viewlift_ai import defaults
from viewlift_ai.services.host.runner import HostError

MAX_JOBS = 40
MAX_WAIT_SECONDS = defaults.JOB_MAX_WAIT_SECONDS
_SUMMARY_LIMIT = 40

_ERROR_RE = re.compile(r"(?:^|\s)(?:error|fatal error):\s", re.IGNORECASE)
_WARNING_RE = re.compile(r"(?:^|\s)warning:\s", re.IGNORECASE)
_TEST_FAIL_RE = re.compile(r"Test Case .+ failed|✘ Test .+ failed|error: -\[.+\] : ")
_TEST_PASS_RE = re.compile(r"Test Case .+ passed|✔ Test .+ passed")
_RESULT_RE = re.compile(
    r"\*\* (BUILD|TEST|CLEAN|ARCHIVE) (SUCCEEDED|FAILED|INTERRUPTED) \*\*"
    r"|Executed \d+ tests?, with \d+ failures?.*"
    r"|Test run with \d+ tests? .*"
    r"|Build complete!|error: fatalError"
)


def condense_log(text: str) -> dict[str, Any]:
    """Keep errors, warnings, test failures, and result lines; drop compiler chatter."""
    errors: list[str] = []
    warnings: list[str] = []
    failures: list[str] = []
    results: list[str] = []
    passed = 0
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in seen:
            continue
        if _TEST_FAIL_RE.search(line):
            failures.append(line)
        elif _ERROR_RE.search(line):
            errors.append(line)
        elif _WARNING_RE.search(line):
            warnings.append(line)
        elif _TEST_PASS_RE.search(line):
            passed += 1
            continue
        elif _RESULT_RE.search(line):
            results.append(line)
        else:
            continue
        seen.add(line)
    return {
        "errors": errors[:_SUMMARY_LIMIT],
        "error_count": len(errors),
        "warnings": warnings[:_SUMMARY_LIMIT],
        "warning_count": len(warnings),
        "test_failures": failures[:_SUMMARY_LIMIT],
        "test_failure_count": len(failures),
        "tests_passed": passed,
        "result_lines": results[-10:],
    }


class JobCancelled(Exception):
    pass


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    title: str
    log_path: Path
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    state: str = "running"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    process: subprocess.Popen[str] | None = None
    cancel_requested: bool = False
    done: threading.Event = field(default_factory=threading.Event)

    def brief(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "job_id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "duration_s": round(end - self.started_at, 1),
            "log_path": str(self.log_path),
        }


class JobContext:
    """Passed to a job body. `run` streams one argv into the job log and returns its output."""

    def __init__(self, job: Job) -> None:
        self.job = job

    def log(self, text: str) -> None:
        with self.job.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else f"{text}\n")

    def check_cancel(self) -> None:
        if self.job.cancel_requested:
            raise JobCancelled()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout: float = 3600.0,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        self.check_cancel()
        args = [str(item) for item in argv]
        self.log(f"$ {' '.join(args)}")
        start = self.job.log_path.stat().st_size if self.job.log_path.exists() else 0
        merged = {**os.environ, **env} if env else None
        with self.job.log_path.open("a", encoding="utf-8") as handle:
            try:
                process = subprocess.Popen(
                    args,
                    cwd=str(cwd) if cwd else None,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=merged,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                raise HostError(f"{args[0]} not found.") from exc
            self.job.process = process
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate(process)
                code = -1
            finally:
                self.job.process = None
        self.check_cancel()
        with self.job.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(start)
            output = handle.read()
        return code, output

    def spawn(self, argv: Sequence[str], *, cwd: str | Path | None = None) -> subprocess.Popen[str]:
        """Start a process that keeps running after the job body returns (app run, recording)."""
        args = [str(item) for item in argv]
        self.log(f"$ {' '.join(args)} &")
        handle = self.job.log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            handle.close()
            raise HostError(f"{args[0]} not found.") from exc
        self.job.process = process
        return process


def _terminate(process: subprocess.Popen[str], sig: int = signal.SIGTERM) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


JobBody = Callable[[JobContext], dict[str, Any]]


class JobStore:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def start(self, kind: str, title: str, body: JobBody, *, background: bool = True) -> Job:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, kind=kind, title=title, log_path=self._log_dir / f"{job_id}.log")
        job.log_path.touch()
        with self._lock:
            self._jobs[job_id] = job
            finished = [key for key, item in self._jobs.items() if item.state != "running"]
            for key in finished[: max(0, len(self._jobs) - MAX_JOBS)]:
                self._jobs.pop(key, None)
        thread = threading.Thread(target=self._run, args=(job, body), daemon=True)
        thread.start()
        if not background:
            job.done.wait()
        return job

    def _run(self, job: Job, body: JobBody) -> None:
        context = JobContext(job)
        try:
            job.result = body(context) or {}
            job.state = "succeeded" if job.result.get("ok", True) else "failed"
        except JobCancelled:
            job.state = "cancelled"
        except HostError as exc:
            job.state = "failed"
            job.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - job bodies must never kill the console
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()
            job.done.set()

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get((job_id or "").strip())
        if job is None:
            raise HostError("Unknown job_id. Call list_jobs.")
        return job

    def describe(self, job_id: str, *, log_tail: int = 0) -> dict[str, Any]:
        job = self.get(job_id)
        out = job.brief()
        if job.state != "running":
            out["result"] = job.result
            if job.error:
                out["error"] = job.error
        if log_tail > 0:
            try:
                text = job.log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            out["log_tail"] = "\n".join(text.splitlines()[-min(log_tail, 400) :])
        return out

    def wait(self, job_id: str, timeout_s: float = defaults.JOB_WAIT_SECONDS) -> dict[str, Any]:
        job = self.get(job_id)
        job.done.wait(max(0.0, min(float(timeout_s), MAX_WAIT_SECONDS)))
        return self.describe(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        job.cancel_requested = True
        if job.process is not None:
            _terminate(job.process, signal.SIGINT)
        job.done.wait(10)
        return job.brief()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.brief() for job in reversed(jobs)]
