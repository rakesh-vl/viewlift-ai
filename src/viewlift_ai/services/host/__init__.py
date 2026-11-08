"""Mac-side developer tools: Xcode builds, simulators, devices, LLDB, SwiftPM, docs, previews.

Every subprocess runs from an argv list (never a shell). Paths an agent passes must sit inside a
configured project root. Handlers return plain dicts; the MCP layer scrubs them before an agent
sees them.
"""

from viewlift_ai.services.host.toolkit import HostToolkit, ToolSpec

__all__ = ["HostToolkit", "ToolSpec"]
