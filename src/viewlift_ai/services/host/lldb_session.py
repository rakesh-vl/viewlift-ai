"""Long-lived LLDB sessions for simulator and device apps. One `xcrun lldb` child per session id."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

from viewlift_ai import defaults
from viewlift_ai.services.host import devicectl, simctl
from viewlift_ai.services.host.runner import HostError, check_bundle_id

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

MAX_OUTPUT = 24_000
DEFAULT_TIMEOUT = 20.0
# These run arbitrary code on the Mac rather than inside the debugged app.
_BLOCKED_COMMAND_RE = re.compile(
    r"^\s*(platform\s+(shell|process\s+launch|put-file|get-file)|script\b|command\s+script\b|"
    r"shell\b|command\s+source\b|settings\s+set\s+target\.(env-vars|run-args)|"
    r"target\s+create\b|process\s+launch\b|run\b|r\b|quit\b|q\b|exit\b|_regexp-)"
)
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class LldbSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.pid: int | None = None
        self.bundle_id = ""
        self._seq = 0
        self._buffer: list[str] = []
        self._cond = threading.Condition()
        self._lock = threading.Lock()
        try:
            self._proc = subprocess.Popen(
                ["xcrun", "lldb", "--no-use-colors", "--no-lldbinit"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise HostError("lldb not found. Install Xcode command-line tools.") from exc
        threading.Thread(target=self._reader, daemon=True).start()
        self.command("settings set auto-confirm true")
        self.command("settings set stop-line-count-before 2")
        self.command("settings set stop-line-count-after 2")

    def _reader(self) -> None:
        stream = self._proc.stdout
        if stream is None:
            return
        for line in stream:
            with self._cond:
                self._buffer.append(line)
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def command(self, text: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
        if not self.alive:
            raise HostError("LLDB session ended. Attach again.")
        with self._lock:
            self._seq += 1
            marker = f"__VL_DONE_{self._seq}__"
            stdin = self._proc.stdin
            if stdin is None:
                raise HostError("LLDB stdin is closed.")
            with self._cond:
                start = len(self._buffer)
            stdin.write(f"{text}\n")
            stdin.write(f"script print('{marker}')\n")
            stdin.flush()
            deadline = time.monotonic() + timeout
            with self._cond:
                while True:
                    consumed = len(self._buffer)
                    chunk = "".join(self._buffer[start:consumed])
                    if f"\n{marker}" in chunk or chunk.startswith(marker):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self.alive:
                        return _clean(chunk, marker, text) + "\n[timed out waiting for lldb]"
                    self._cond.wait(remaining)
                del self._buffer[:consumed]
            return _clean(chunk, marker, text)

    def drain(self) -> str:
        with self._cond:
            text = "".join(self._buffer)
            self._buffer.clear()
        return text[-MAX_OUTPUT:]

    def close(self) -> None:
        if not self.alive:
            return
        try:
            if self.pid:
                self.command("process detach", timeout=5)
            stdin = self._proc.stdin
            if stdin is not None:
                stdin.write("quit\n")
                stdin.flush()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired, HostError):
            self._proc.kill()


def _clean(chunk: str, marker: str, command: str) -> str:
    lines = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if (
            marker in stripped
            or stripped.startswith("(lldb) script print(")
            or stripped == "(lldb)"
        ):
            continue
        if stripped == f"(lldb) {command}".strip():
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines).strip()
    return text[-MAX_OUTPUT:]


class LldbManager:
    def __init__(self, ctx: HostContext) -> None:
        self.ctx = ctx
        self._sessions: dict[str, LldbSession] = {}
        self._lock = threading.Lock()

    def _session_id(self, args: dict[str, Any]) -> str:
        value = str(args.get("sessionId") or "default")
        if not _SESSION_RE.match(value):
            raise HostError("sessionId uses letters, digits, - and _.")
        return value

    def get(self, args: dict[str, Any]) -> LldbSession:
        session_id = self._session_id(args)
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or not session.alive:
            raise HostError("No LLDB session. Call debug_attach_sim or debug_attach_device first.")
        return session

    def _sim_pid(self, udid: str, bundle: str) -> int:
        result = self.ctx.run_ok(
            ["xcrun", "simctl", "spawn", udid, "launchctl", "list"], timeout=30
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and f"UIKitApplication:{bundle}[" in parts[2] and parts[0].isdigit():
                return int(parts[0])
        raise HostError(f"{bundle} is not running on this simulator. Launch it first.")

    def attach(self, args: dict[str, Any]) -> dict[str, Any]:
        pid = args.get("pid")
        bundle = ""
        if not isinstance(pid, int):
            bundle = check_bundle_id(str(self.ctx.defaults.merged(args).get("bundleId", "")))
            sim = simctl.resolve_simulator(self.ctx, args)
            pid = self._sim_pid(sim["udid"], bundle)
        return self._attach(args, int(pid), bundle, [])

    def attach_device(self, args: dict[str, Any]) -> dict[str, Any]:
        """Attach through Xcode's LLDB `device` commands (Xcode 16 or later, iOS/tvOS 17+)."""
        device = devicectl.resolve_device(self.ctx, args)
        udid = str(device.get("identifier") or device.get("udid"))
        pid = args.get("pid")
        bundle = ""
        if not isinstance(pid, int):
            bundle = check_bundle_id(str(self.ctx.defaults.merged(args).get("bundleId", "")))
            pid = devicectl.find_pid(self.ctx, device, bundle)
            if pid is None:
                raise HostError(
                    f"{bundle} is not running on {device.get('name')}. Launch it first."
                )
        return self._attach(args, int(pid), bundle, [f"device select {udid}"], device_attach=True)

    def _attach(
        self,
        args: dict[str, Any],
        pid: int,
        bundle: str,
        setup: list[str],
        *,
        device_attach: bool = False,
    ) -> dict[str, Any]:
        session_id = self._session_id(args)
        with self._lock:
            old = self._sessions.pop(session_id, None)
        if old is not None:
            old.close()
        session = LldbSession(session_id)
        for line in setup:
            selected = session.command(line, timeout=60)
            if "is not a valid command" in selected:
                session.close()
                raise HostError("This LLDB has no device commands. Install Xcode 16 or later.")
            if "error:" in selected:
                session.close()
                raise HostError(f"Device select failed: {selected[-600:]}")
        verb = "device process attach" if device_attach else "process attach"
        output = session.command(f"{verb} --pid {pid}", timeout=90)
        if "error:" in output and "stopped" not in output:
            session.close()
            raise HostError(f"Attach failed: {output[-600:]}")
        session.pid = int(pid)
        session.bundle_id = bundle
        with self._lock:
            self._sessions[session_id] = session
        out = {"sessionId": session_id, "pid": pid, "bundleId": bundle, "output": output[-4000:]}
        if args.get("continueAfterAttach", True):
            out["continued"] = self._continue(session)
        return out

    def _continue(self, session: LldbSession) -> str:
        session.command("script lldb.debugger.SetAsync(True)")
        output = session.command("process continue")
        session.command("script lldb.debugger.SetAsync(False)")
        return output

    def breakpoint_add(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        file = str(args.get("file") or "")
        function = str(args.get("function") or "")
        if file:
            line = int(args.get("line") or 0)
            if line <= 0 or not re.fullmatch(r"[\w./ +-]{1,300}", file):
                raise HostError("file needs a positive line and a plain path.")
            command = f'breakpoint set --file "{file}" --line {line}'
        elif function:
            if not re.fullmatch(r"[\w.:$<>()\[\] ,-]{1,300}", function):
                raise HostError("function has unsupported characters.")
            command = f'breakpoint set --name "{function}"'
        else:
            raise HostError("Pass file and line, or function.")
        condition = str(args.get("condition") or "")
        output = session.command(command)
        match = re.search(r"Breakpoint (\d+):", output)
        if match and condition:
            if "\n" in condition or len(condition) > 300:
                raise HostError("condition is too long.")
            session.command(
                f"breakpoint modify -c '{condition.replace(chr(39), '')}' {match.group(1)}"
            )
        return {"breakpointId": int(match.group(1)) if match else None, "output": output}

    def breakpoint_remove(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        breakpoint_id = int(args.get("breakpointId") or 0)
        if breakpoint_id <= 0:
            raise HostError("breakpointId is required.")
        return {"output": session.command(f"breakpoint delete {breakpoint_id}")}

    def resume(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        session.drain()
        output = self._continue(session)
        wait = max(0.0, min(float(args.get("waitForStopSeconds", 0)), defaults.JOB_WAIT_SECONDS))
        stopped = ""
        if wait:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                time.sleep(0.25)
                text = session.drain()
                if text:
                    stopped += text
                if "stop reason" in stopped:
                    break
        return {"output": output, "stop_events": stopped[-6000:]}

    def stack(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        frames = max(1, min(int(args.get("maxFrames", 25)), 200))
        if args.get("allThreads"):
            return {"output": session.command(f"thread backtrace all -c {frames}", timeout=30)}
        thread = args.get("threadIndex")
        if isinstance(thread, int):
            session.command(f"thread select {thread}")
        return {"output": session.command(f"thread backtrace -c {frames}")}

    def variables(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        frame = int(args.get("frameIndex", 0))
        session.command(f"frame select {frame}")
        depth = max(0, min(int(args.get("depth", 1)), 4))
        return {"output": session.command(f"frame variable --depth {depth} --no-summary-depth")}

    def raw(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self.get(args)
        command = str(args.get("command") or "").strip()
        if not command or "\n" in command or len(command) > 1000:
            raise HostError("command is one line of LLDB input.")
        if _BLOCKED_COMMAND_RE.match(command):
            raise HostError("That LLDB command can run code on the Mac and is not allowed.")
        timeout = max(1.0, min(float(args.get("timeout_s", DEFAULT_TIMEOUT)), 60.0))
        return {"output": session.command(command, timeout=timeout)}

    def detach(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = self._session_id(args)
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return {"detached": False, "detail": "No session."}
        session.close()
        return {"detached": True, "sessionId": session_id}

    def list(self, _args: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = [
                {"sessionId": key, "pid": item.pid, "bundleId": item.bundle_id, "alive": item.alive}
                for key, item in self._sessions.items()
            ]
        return {"sessions": rows}


def register(toolkit: HostToolkit) -> None:
    manager = LldbManager(toolkit.ctx)
    toolkit.lldb = manager  # type: ignore[attr-defined]
    toolkit.add("debug_attach_sim", manager.attach, mutating=True)
    toolkit.add("debug_attach_device", manager.attach_device, mutating=True)
    toolkit.add("debug_breakpoint_add", manager.breakpoint_add, mutating=True)
    toolkit.add("debug_breakpoint_remove", manager.breakpoint_remove, mutating=True)
    toolkit.add("debug_continue", manager.resume, mutating=True)
    toolkit.add("debug_stack", manager.stack, mutating=True)
    toolkit.add("debug_variables", manager.variables, mutating=True)
    toolkit.add("debug_lldb_command", manager.raw, mutating=True)
    toolkit.add("debug_detach", manager.detach, mutating=True)
    toolkit.add("debug_sessions", manager.list)
