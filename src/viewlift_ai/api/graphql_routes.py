"""In-app GraphQL playground. Loopback only. Passwords never leave this Mac."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.api.scope import scope_kwargs
from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.services.graphql_client import GraphQLPlayground, SchemaCache
from viewlift_ai.services.graphql_store import (
    GRAPHQL_ENDPOINTS_FILENAME,
    GraphQLEndpointStore,
    GraphQLError,
    graphql_bases_from_flows,
)

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
MAX_BODY_BYTES = 1_048_576

router = APIRouter()


def _require_loopback(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="GraphQL playground is loopback-only")


def _require_json(request: Request) -> None:
    _require_loopback(request)
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


def _playground(request: Request) -> GraphQLPlayground:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=503, detail="GraphQL playground unavailable")
    cache = getattr(request.app.state, "graphql_schema_cache", None)
    if not isinstance(cache, SchemaCache):
        cache = SchemaCache()
        request.app.state.graphql_schema_cache = cache
    store = GraphQLEndpointStore(
        config.data_dir / GRAPHQL_ENDPOINTS_FILENAME,
        SecretBoxCipher(config.secret.key),
    )
    opener = getattr(request.app.state, "graphql_opener", None)
    return GraphQLPlayground(store, cache, opener)


async def _payload(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request is larger than 1 MB.")
    try:
        parsed = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    return parsed


def _call(action):
    try:
        return action()
    except GraphQLError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/graphql", response_class=HTMLResponse)
async def graphql_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "graphql.html",
        {"title": "GraphQL", "active_nav": "graphql"},
    )


@router.get("/api/graphql/bases")
def graphql_bases(
    request: Request,
    client_id: Annotated[list[str] | None, Query()] = None,
    device_key: Annotated[str | None, Query()] = None,
    bundle_id: Annotated[str | None, Query()] = None,
    auth_state: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    profile_id: Annotated[str | None, Query()] = None,
    anonymous_id: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """Distinct GraphQL origins in captured traffic, newest first."""
    _require_loopback(request)
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="GraphQL playground unavailable")
    flows = store.list_flows(
        limit=200,
        kind="graphql",
        **scope_kwargs(
            request,
            client_id,
            device_key,
            bundle_id,
            auth_state,
            user_id,
            profile_id,
            anonymous_id,
        ),
    )
    return {"bases": graphql_bases_from_flows(flows)}


@router.get("/api/graphql/endpoints")
def list_endpoints(request: Request) -> dict[str, object]:
    _require_loopback(request)
    return _call(lambda: _playground(request).list_endpoints())


@router.put("/api/graphql/endpoints")
async def save_endpoints(request: Request) -> dict[str, object]:
    _require_json(request)
    payload = await _payload(request)
    return _call(lambda: _playground(request).save_endpoints(payload))


@router.post("/api/graphql/execute")
async def execute_graphql(request: Request) -> dict[str, Any]:
    _require_json(request)
    payload = await _payload(request)
    return _call(lambda: _playground(request).execute(payload))


@router.post("/api/graphql/schema")
async def graphql_schema(request: Request) -> dict[str, Any]:
    _require_json(request)
    payload = await _payload(request)
    return _call(lambda: _playground(request).schema(payload))
