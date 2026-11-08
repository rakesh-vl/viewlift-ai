"""Optional tool workflows (build, targets, simulator, device, UI, LLDB, SwiftPM, docs, previews).

Every workflow tool stays registered. `tools/list` returns only the debug tools plus the workflows
enabled for that MCP session, so one chat does not pay for another chat's tools.
`VIEWLIFT_AI_MCP_WORKFLOWS` is the starting set for a new session ("all" turns every group on).
An empty value starts with the debug tools only. `manage_workflows` changes one session.
"""

from __future__ import annotations

import os
import threading
import weakref
from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server.catalog import TOOL_GROUPS, WORKFLOW_IDS
from viewlift_ai.mcp_server.workflows import (
    build,
    debugger,
    docs,
    previews,
    simulator,
    targets,
    ui,
)
from viewlift_ai.mcp_server.workflows.common import RunFn

WORKFLOWS_ENV = "VIEWLIFT_AI_MCP_WORKFLOWS"


def tools_by_workflow() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group in TOOL_GROUPS:
        if group.workflow != "core":
            out.setdefault(group.workflow, []).extend(tool.name for tool in group.tools)
    return out


def env_workflows() -> set[str]:
    """Starting workflows for a session that has not called manage_workflows."""
    raw = os.environ.get(WORKFLOWS_ENV, "").strip().lower()
    if not raw:
        return set()
    if raw == "all":
        return set(WORKFLOW_IDS)
    return {part.strip() for part in raw.split(",") if part.strip() in WORKFLOW_IDS}


class WorkflowGate:
    """Per-session workflow sets. The tool manager keeps every tool registered."""

    def __init__(self) -> None:
        self._sessions: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()
        self._lock = threading.Lock()
        self._optional = {
            name: workflow for workflow, names in tools_by_workflow().items() for name in names
        }

    def _stored(self, session: Any) -> set[str] | None:
        if session is None:
            return None
        try:
            with self._lock:
                return self._sessions.get(session)
        except TypeError:
            return None

    def enabled_for(self, session: Any) -> set[str]:
        stored = self._stored(session)
        if stored is None:
            return env_workflows()
        return set(stored)

    def apply(self, session: Any, enabled: set[str]) -> None:
        """Remember one session's workflows. No session means nothing is stored."""
        if session is None:
            return
        cleaned = set(enabled) & set(WORKFLOW_IDS)
        try:
            with self._lock:
                self._sessions[session] = cleaned
        except TypeError:
            return

    def is_listed(self, name: str, session: Any) -> bool:
        workflow = self._optional.get(name)
        if workflow is None:
            return True
        return workflow in self.enabled_for(session)

    def status(self, session: Any) -> dict[str, Any]:
        enabled = self.enabled_for(session)
        groups = tools_by_workflow()
        return {
            "enabled": sorted(enabled),
            "scope": "session",
            "available": [
                {
                    "workflow": key,
                    "enabled": key in enabled,
                    "tools": len(groups.get(key, [])),
                }
                for key in WORKFLOW_IDS
            ],
            "env": WORKFLOWS_ENV,
        }


def _current_session(mcp: FastMCP) -> Any:
    try:
        return mcp.get_context().session
    except (LookupError, ValueError):
        return None


def _install_list_filter(mcp: FastMCP, gate: WorkflowGate) -> None:
    """tools/list returns this session's tools. Calls to a hidden tool still run."""
    original = mcp.list_tools

    async def list_tools() -> list[Any]:
        listed = await original()
        session = _current_session(mcp)
        return [tool for tool in listed if gate.is_listed(tool.name, session)]

    mcp._mcp_server.list_tools()(list_tools)


def register_all(mcp: FastMCP, run: RunFn) -> WorkflowGate:
    for module in (build, targets, simulator, ui, debugger, docs, previews):
        module.register(mcp, run)
    gate = WorkflowGate()
    _install_list_filter(mcp, gate)
    return gate
