"""Setup page and APIs: CA download tokens, mobileconfig, LAN bind, simulator helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from viewlift_ai.services.setup import SetupService, SetupValidationError

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


def _setup(request: Request) -> SetupService:
    svc = getattr(request.app.state, "setup", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Setup service unavailable")
    return svc


def _mutating_enabled(request: Request) -> bool:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        return False
    return bool(engine.any_mutating_enabled())


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "setup.html",
        {
            "title": "Setup",
            "active_nav": "setup",
            "mutating_enabled": _mutating_enabled(request),
        },
    )


@router.get("/api/setup/status")
def setup_status(request: Request) -> dict[str, object]:
    status = _setup(request).status()
    runner = getattr(request.app.state, "proxy_runner", None)
    status["proxy_running"] = bool(runner and runner.is_running)
    reveal = getattr(request.app.state, "reveal", None)
    if reveal is not None:
        status["reveal"] = reveal.status()
    return status


@router.post("/api/setup/ca/ensure")
def ensure_ca(request: Request) -> dict[str, object]:
    try:
        return _setup(request).ensure_ca()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/setup/ca/token")
def mint_ca_token(request: Request) -> dict[str, object]:
    try:
        _setup(request).ensure_ca()
        return _setup(request).mint_ca_download_token()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/setup/ca/cert")
def download_ca_cert(
    request: Request,
    token: Annotated[str, Query(min_length=8)],
) -> Response:
    return _download(request, token)


@router.post("/api/setup/mobileconfig/token")
async def mint_mobileconfig_token(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    host = body.get("host") or body.get("proxy_host")
    port = body.get("port") or body.get("proxy_port")
    try:
        _setup(request).ensure_ca()
        return _setup(request).mint_mobileconfig_token(
            proxy_host=str(host or ""),
            proxy_port=int(port) if port is not None else -1,
        )
    except SetupValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/setup/mobileconfig")
def download_mobileconfig(
    request: Request,
    token: Annotated[str, Query(min_length=8)],
) -> Response:
    return _download(request, token)


@router.post("/api/setup/proxy")
async def configure_proxy(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    host = str(body.get("host") or "")
    port = body.get("port")
    enable_lan = bool(body.get("enable_lan", False))
    svc = _setup(request)
    try:
        bind = svc.configure_proxy_bind(
            host=host,
            port=int(port) if port is not None else -1,
            enable_lan=enable_lan,
        )
    except SetupValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reconfigure = getattr(request.app.state, "reconfigure_proxy", None)
    if reconfigure is None:
        raise HTTPException(status_code=503, detail="Proxy reconfigure unavailable")
    try:
        await reconfigure(
            host=bind.host,
            port=bind.port,
            proxy_auth=(
                (bind.auth.username, bind.auth.password) if bind.auth is not None else None
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to rebind proxy: {exc}") from exc

    auth = None
    if bind.auth is not None:
        auth = {"username": bind.auth.username, "password": bind.auth.password}
    return {
        "host": bind.host,
        "port": bind.port,
        "lan_enabled": bind.lan_enabled,
        "auth_required": bind.auth is not None,
        "auth": auth,
    }


@router.post("/api/setup/simulator/install-ca")
def simulator_install_ca(request: Request) -> dict[str, object]:
    result = _setup(request).install_simulator_ca()
    return {
        "ok": result.ok,
        "detail": result.detail,
        "argv": result.argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@router.post("/api/setup/simulator/configure-proxy")
def simulator_configure_proxy(request: Request) -> dict[str, object]:
    result = _setup(request).configure_simulator_proxy()
    return {
        "ok": result.ok,
        "detail": result.detail,
        "argv": result.argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@router.post("/api/setup/test")
async def run_test(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    url = str(body.get("url") or "http://example.com/")
    result = _setup(request).run_test_request(url=url)
    return {
        "ok": result.ok,
        "status_code": result.status_code,
        "detail": result.detail,
        "via_proxy": result.via_proxy,
    }


@router.get("/api/setup/test")
def test_status(request: Request) -> dict[str, object]:
    last = _setup(request).status().get("last_test")
    return {"last_test": last}


def _ingest(request: Request):
    svc = getattr(request.app.state, "ingest", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Ingest service unavailable")
    return svc


@router.post("/api/setup/sdk/enable")
async def enable_sdk_receiver(request: Request) -> dict[str, object]:
    body = await _json_body(request)
    host = str(body.get("host") or "")
    iface = str(body.get("iface") or "")
    port = body.get("port")
    try:
        return _ingest(request).enable(
            host=host,
            port=int(port) if port is not None else 8742,
            iface=iface or None,
        )
    except SetupValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/setup/sdk/disable")
def disable_sdk_receiver(request: Request) -> dict[str, object]:
    return _ingest(request).disable()


@router.post("/api/setup/sdk/pairing-token")
def mint_sdk_pairing_token(request: Request) -> dict[str, object]:
    try:
        return _ingest(request).mint_pairing_token()
    except SetupValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



def _download(request: Request, token: str) -> Response:
    svc = _setup(request)
    try:
        entry = svc.consume_download(token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return Response(
        content=entry.payload,
        media_type=entry.media_type,
        headers={
            **_NO_STORE,
            "Content-Disposition": f'attachment; filename="{entry.filename}"',
        },
    )


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body
