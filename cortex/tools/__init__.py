"""Atlas Cortex developer tools — sandboxed execution, code analysis, function calling."""

# Module ownership: Developer tools and function calling

from __future__ import annotations

from cortex.tools.function_calling import (
    FunctionCallingHandler,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    "FunctionCallingHandler",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
]

def __getattr__(name: str):
    """Keep optional local documentation extensions out of core imports."""
    if name not in {"DocBulkLookupTool", "DocLookup", "DocLookupTool", "DocResult"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module("cortex.tools.doc_lookup"), name)
