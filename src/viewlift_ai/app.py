"""Process entrypoint: FastAPI + optional mitmproxy (loopback by default)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from viewlift_ai import __version__
from viewlift_ai.api.debug_routes import router as debug_router
from viewlift_ai.api.logs_routes import router as logs_router
from viewlift_ai.api.routes import router as network_router
from viewlift_ai.api.rules_routes import router as rules_router
from viewlift_ai.api.setup_routes import router as setup_router
from viewlift_ai.api.ws import ws_router
from viewlift_ai.config import AppConfig, load_config
from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.proxy.allowlist import HostAllowlist
from viewlift_ai.proxy.breakpoints import BreakpointRegistry
from viewlift_ai.proxy.ca import ensure_ca
from viewlift_ai.proxy.redact import RedactionService
from viewlift_ai.proxy.runner import ProxyRunner
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.debug_session import DebugSessionService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus, FlowEventBus, LogEventBus
from viewlift_ai.services.ingest import IngestService
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.log_tail import LogTailService
from viewlift_ai.services.reveal import RevealSession
from viewlift_ai.services.rule_engine import RuleEngine
from viewlift_ai.services.rule_store import RULES_FILENAME, RuleConfigStore
from viewlift_ai.services.setup import SetupService

_config: AppConfig | None = None
_store: EncryptedSqliteStore | None = None
_event_bus: FlowEventBus | None = None
_log_event_bus: LogEventBus | None = None
_debug_event_bus: EventBus[dict[str, object]] | None = None
_proxy_runner: ProxyRunner | None = None
_allowlist: HostAllowlist | None = None
_capture: CaptureService | None = None
_log_capture: LogCaptureService | None = None
_log_tail: LogTailService | None = None
_ingest: IngestService | None = None
_redaction: RedactionService | None = None
_rule_engine: RuleEngine | None = None
_breakpoints: BreakpointRegistry | None = None
_setup: SetupService | None = None
_reveal: RevealSession | None = None
_debug_sessions: DebugSessionService | None = None

WEB_STATIC = Path(__file__).resolve().parent / "web" / "static"


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_store() -> EncryptedSqliteStore:
    global _store
    if _store is None:
        cfg = get_config()
        cipher = SecretBoxCipher(cfg.secret.key)
        _store = EncryptedSqliteStore(
            cfg.database_path,
            cipher,
            retention=cfg.retention,
        )
        _store.apply_retention_cleanup()
    return _store


def get_event_bus() -> FlowEventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = FlowEventBus()
    return _event_bus


def get_log_event_bus() -> LogEventBus:
    global _log_event_bus
    if _log_event_bus is None:
        _log_event_bus = LogEventBus()
    return _log_event_bus


def get_debug_event_bus() -> EventBus[dict[str, object]]:
    global _debug_event_bus
    if _debug_event_bus is None:
        _debug_event_bus = EventBus()
    return _debug_event_bus


def get_debug_sessions() -> DebugSessionService:
    global _debug_sessions
    if _debug_sessions is None:
        _debug_sessions = DebugSessionService(
            store=get_store(),
            events=get_debug_event_bus(),
        )
    return _debug_sessions


def get_rule_engine() -> RuleEngine:
    global _rule_engine
    if _rule_engine is None:
        cfg = get_config()
        store = RuleConfigStore(cfg.data_dir / RULES_FILENAME)
        engine = RuleEngine(store)
        # Seed allowlist from env/config when persisted file has none.
        if not engine.config.allowlist and cfg.allowlist_hosts:
            engine.set_allowlist(list(cfg.allowlist_hosts))
        engine.subscribe(_on_rules_changed)
        _rule_engine = engine
    return _rule_engine


def get_allowlist() -> HostAllowlist:
    global _allowlist
    if _allowlist is None:
        patterns = get_rule_engine().config.allowlist or list(get_config().allowlist_hosts)
        _allowlist = HostAllowlist(patterns)
    return _allowlist


def get_redaction() -> RedactionService:
    global _redaction
    if _redaction is None:
        _redaction = RedactionService()
    return _redaction


def get_reveal() -> RevealSession:
    global _reveal
    if _reveal is None:
        _reveal = RevealSession()
    return _reveal


def get_capture() -> CaptureService:
    global _capture
    if _capture is None:
        _capture = CaptureService(
            get_store(),
            get_event_bus(),
            get_redaction(),
            reveal=get_reveal(),
        )
    return _capture


def get_log_capture() -> LogCaptureService:
    global _log_capture
    if _log_capture is None:
        _log_capture = LogCaptureService(get_store(), get_log_event_bus())
    return _log_capture


def get_log_tail() -> LogTailService:
    global _log_tail
    if _log_tail is None:
        _log_tail = LogTailService(capture=get_log_capture())
    return _log_tail


def get_ingest() -> IngestService:
    global _ingest
    if _ingest is None:
        cfg = get_config()
        _ingest = IngestService(
            capture=get_capture(),
            log_capture=get_log_capture(),
            cert_dir=cfg.data_dir / "ingest",
            redaction=get_redaction(),
            debug_sessions=get_debug_sessions(),
        )
    return _ingest


def get_breakpoints() -> BreakpointRegistry:
    global _breakpoints
    if _breakpoints is None:
        _breakpoints = BreakpointRegistry()
    return _breakpoints


def get_setup() -> SetupService:
    global _setup
    if _setup is None:
        cfg = get_config()
        _setup = SetupService(
            confdir=cfg.mitm_confdir,
            proxy_host=cfg.proxy_host,
            proxy_port=cfg.proxy_port,
            keychain_fallback_active=cfg.keychain_fallback_active,
        )
        _setup.bind_sdk_status(lambda: get_ingest().status())
    return _setup


def _on_rules_changed(config) -> None:
    allow = _allowlist
    if allow is not None:
        allow.set_patterns(config.allowlist)


def _bind_app_state(application: FastAPI) -> None:
    application.state.store = get_store()
    application.state.event_bus = get_event_bus()
    application.state.log_event_bus = get_log_event_bus()
    application.state.debug_event_bus = get_debug_event_bus()
    application.state.debug_sessions = get_debug_sessions()
    application.state.redaction = get_redaction()
    application.state.config = get_config()
    application.state.rule_engine = get_rule_engine()
    application.state.allowlist = get_allowlist()
    application.state.breakpoints = get_breakpoints()
    application.state.setup = get_setup()
    application.state.log_tail = get_log_tail()
    application.state.log_capture = get_log_capture()
    application.state.ingest = get_ingest()
    application.state.reveal = get_reveal()
    application.state.proxy_runner = _proxy_runner
    application.state.reconfigure_proxy = reconfigure_proxy


async def reconfigure_proxy(
    *,
    host: str,
    port: int,
    proxy_auth: tuple[str, str] | None,
) -> None:
    """Stop and restart mitmproxy on the selected bind; LAN binds require auth."""
    global _proxy_runner
    cfg = get_config()
    cfg.proxy_host = host
    cfg.proxy_port = port
    ensure_ca(cfg.mitm_confdir)
    previous = _proxy_runner
    if previous is not None:
        await asyncio.to_thread(previous.stop)
        _proxy_runner = None
    if not cfg.proxy_enabled:
        app.state.proxy_runner = None
        return
    runner = ProxyRunner(
        listen_host=host,
        listen_port=port,
        confdir=cfg.mitm_confdir,
        allowlist=get_allowlist(),
        capture=get_capture(),
        rule_engine=get_rule_engine(),
        breakpoints=get_breakpoints(),
        proxy_auth=proxy_auth,
    )
    await asyncio.to_thread(runner.start)
    _proxy_runner = runner
    app.state.proxy_runner = runner


def _shutdown_store() -> None:
    store = _store
    cfg = _config
    if store is None:
        return
    if cfg is not None and cfg.retention == RetentionPolicy.SESSION:
        store.wipe_session()
    store.close()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _proxy_runner
    cfg = get_config()
    ensure_ca(cfg.mitm_confdir)
    get_setup()
    ingest = get_ingest()
    if os.environ.get("VIEWLIFT_SDK_WATCH") == "1":
        ingest.start_network_watch()
    get_log_tail()
    _bind_app_state(application)
    loop = asyncio.get_running_loop()
    bus = get_event_bus()
    bus.bind_loop(loop)
    log_bus = get_log_event_bus()
    log_bus.bind_loop(loop)
    get_debug_event_bus().bind_loop(loop)
    breakpoints = get_breakpoints()
    breakpoints.bind_loop(loop)
    runner: ProxyRunner | None = None
    if cfg.proxy_enabled:
        setup = get_setup()
        auth = setup.bind.auth
        runner = ProxyRunner(
            listen_host=cfg.proxy_host,
            listen_port=cfg.proxy_port,
            confdir=cfg.mitm_confdir,
            allowlist=get_allowlist(),
            capture=get_capture(),
            rule_engine=get_rule_engine(),
            breakpoints=breakpoints,
            proxy_auth=(
                (auth.username, auth.password) if auth is not None else None
            ),
        )
        await asyncio.to_thread(runner.start)
        _proxy_runner = runner
        application.state.proxy_runner = runner
    try:
        yield
    finally:
        await breakpoints.shutdown()
        get_log_tail().stop()
        get_ingest().stop_network_watch()
        get_ingest().disable()
        get_reveal().wipe()
        active = _proxy_runner
        if active is not None:
            await asyncio.to_thread(active.stop)
            _proxy_runner = None
        _shutdown_store()


app = FastAPI(title="Viewlift AI", version=__version__, lifespan=lifespan)
app.include_router(network_router)
app.include_router(rules_router)
app.include_router(setup_router)
app.include_router(logs_router)
app.include_router(debug_router)
app.include_router(ws_router)
app.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")


@app.get("/health")
def health() -> dict[str, object]:
    cfg = get_config()
    get_store()
    runner = _proxy_runner
    engine = get_rule_engine()
    ingest = get_ingest()
    reveal = get_reveal()
    return {
        "status": "ok",
        "version": __version__,
        "retention": cfg.retention.value,
        "keychain_fallback": cfg.keychain_fallback_active,
        "data_dir": str(cfg.data_dir),
        "proxy_enabled": cfg.proxy_enabled,
        "proxy_host": cfg.proxy_host,
        "proxy_port": cfg.proxy_port,
        "proxy_running": bool(runner and runner.is_running),
        "allowlist": list(get_allowlist().patterns),
        "mutating_rules_enabled": engine.any_mutating_enabled(),
        "sdk_receiver_enabled": ingest.enabled,
        "log_tail": get_log_tail().status(),
        "reveal": {
            "unlocked": reveal.unlocked,
            "cached_flows": reveal.status()["cached_flows"],
        },
    }


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Test/DI hook: bind an explicit config before first request."""
    global _config, _store, _event_bus, _log_event_bus, _debug_event_bus
    global _proxy_runner, _allowlist, _capture
    global _log_capture, _log_tail, _ingest, _redaction, _rule_engine, _breakpoints, _setup
    global _reveal, _debug_sessions
    if config is not None:
        if _ingest is not None:
            _ingest.disable()
        if _log_tail is not None:
            _log_tail.stop()
        if _reveal is not None:
            _reveal.wipe()
        _config = config
        _store = None
        _event_bus = None
        _log_event_bus = None
        _debug_event_bus = None
        _proxy_runner = None
        _allowlist = None
        _capture = None
        _log_capture = None
        _log_tail = None
        _ingest = None
        _redaction = None
        _rule_engine = None
        _breakpoints = None
        _setup = None
        _reveal = None
        _debug_sessions = None
    get_store()
    get_rule_engine()
    get_breakpoints()
    get_setup()
    get_ingest()
    get_log_tail()
    get_reveal()
    ensure_ca(get_config().mitm_confdir)
    _bind_app_state(app)
    return app


def main() -> None:
    os.environ["VIEWLIFT_SDK_WATCH"] = "1"
    cfg = get_config()
    get_store()
    get_rule_engine()
    uvicorn.run(
        "viewlift_ai.app:app",
        host=cfg.host,
        port=cfg.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
