"""Domain services. No FastAPI imports — callable from API or future MCP."""

# Keep this package init import-light to avoid capture↔proxy circular imports.
__all__: list[str] = []
