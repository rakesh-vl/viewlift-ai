"""Programmatic mitmproxy lifecycle beside uvicorn."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from viewlift_ai.proxy.addon import ViewliftCaptureAddon
from viewlift_ai.proxy.allowlist import HostAllowlist
from viewlift_ai.proxy.breakpoints import BreakpointRegistry
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class _ReadyAddon:
    """Signals when mitmproxy has entered the running state."""

    def __init__(self, ready: threading.Event) -> None:
        self._ready = ready

    def running(self) -> None:
        self._ready.set()


class ProxyRunner:
    """Start/stop DumpMaster on a dedicated thread. Loopback bind by default."""

    def __init__(
        self,
        *,
        listen_host: str,
        listen_port: int,
        confdir: Path,
        allowlist: HostAllowlist,
        capture: CaptureService,
        rule_engine: RuleEngine | None = None,
        breakpoints: BreakpointRegistry | None = None,
        proxy_auth: tuple[str, str] | None = None,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._confdir = confdir
        self._allowlist = allowlist
        self._capture = capture
        self._rule_engine = rule_engine
        self._breakpoints = breakpoints
        self._proxy_auth = proxy_auth
        self._thread: threading.Thread | None = None
        self._master: DumpMaster | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def listen_host(self) -> str:
        return self._listen_host

    @property
    def listen_port(self) -> int:
        return self._listen_port

    @property
    def proxy_auth(self) -> tuple[str, str] | None:
        return self._proxy_auth

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, timeout: float = 15.0) -> None:
        if self.is_running:
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="viewlift-mitmproxy",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self.stop()
            msg = "mitmproxy failed to become ready"
            raise TimeoutError(msg)
        if self._error is not None:
            raise RuntimeError("mitmproxy failed to start") from self._error

    def stop(self, *, timeout: float = 10.0) -> None:
        master = self._master
        if master is not None:
            try:
                master.shutdown()
            except Exception:
                logger.exception("Error signaling mitmproxy shutdown")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._master = None
        self._loop = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        confdir = self._confdir
        confdir.mkdir(parents=True, exist_ok=True)
        try:
            opts = Options(
                listen_host=self._listen_host,
                listen_port=self._listen_port,
                confdir=str(confdir),
            )
            master = DumpMaster(
                opts,
                loop=loop,
                with_termlog=False,
                with_dumper=False,
            )
            master.addons.add(
                _ReadyAddon(self._ready),
                ViewliftCaptureAddon(
                    self._allowlist,
                    self._capture,
                    rule_engine=self._rule_engine,
                    breakpoints=self._breakpoints,
                ),
            )
            if self._proxy_auth is not None:
                user, password = self._proxy_auth
                master.options.update(proxyauth=f"{user}:{password}")
            self._master = master
            loop.run_until_complete(master.run())
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            logger.exception("mitmproxy thread crashed")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
