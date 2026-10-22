"""Services and proxy helpers must stay framework-free for future MCP reuse."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "viewlift_ai"
FORBIDDEN = {"fastapi", "starlette", "uvicorn"}
# mitmproxy hook/runner modules are allowed to depend on mitmproxy only.
PROXY_SKIP = {"addon.py", "runner.py"}


def _offenders_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN:
                    found.append(f"{path.name}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN:
                found.append(f"{path.name}: from {node.module}")
    return found


def test_services_do_not_import_fastapi() -> None:
    offenders: list[str] = []
    for path in (ROOT / "services").rglob("*.py"):
        offenders.extend(_offenders_in(path))
    assert offenders == []


def test_proxy_helpers_do_not_import_fastapi() -> None:
    offenders: list[str] = []
    for path in (ROOT / "proxy").rglob("*.py"):
        if path.name in PROXY_SKIP:
            continue
        offenders.extend(_offenders_in(path))
    assert offenders == []
