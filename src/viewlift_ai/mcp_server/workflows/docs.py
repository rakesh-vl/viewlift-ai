"""Apple Developer Documentation search and reader."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server.workflows.common import READ_WEB, RunFn, host


def register(mcp: FastMCP, run: RunFn) -> None:
    def call(name: str, args: dict[str, Any]) -> Any:
        return run(name, args, lambda: host(name, args))

    @mcp.tool(annotations=READ_WEB)
    def apple_docs_list_frameworks(filter: str = "") -> dict[str, Any]:  # noqa: A002
        """Apple frameworks with their slug (swiftui, avkit, ...) for apple_docs_search."""
        return call("apple_docs_list_frameworks", {"filter": filter})

    @mcp.tool(annotations=READ_WEB)
    def apple_docs_search(
        query: str, frameworks: list[str] | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Search Apple symbols and articles. Exact names (GridItem), prefixes, wildcards
        (Lazy*, *Style), or keywords (tab view). Default frameworks: swiftui, uikit, foundation."""
        return call(
            "apple_docs_search", {"query": query, "frameworks": frameworks or [], "limit": limit}
        )

    @mcp.tool(annotations=READ_WEB)
    def apple_docs_get(path: str, maxChars: int = 8000) -> dict[str, Any]:
        """Read one page: declaration, availability, overview, parameters, and topics."""
        return call("apple_docs_get", {"path": path, "maxChars": maxChars})
