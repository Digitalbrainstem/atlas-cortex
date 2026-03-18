"""Atlas CLI — Terminal-based AI assistant with chat and agent modes.

Usage::

    python -m cortex.cli chat      # Interactive chat
    python -m cortex.cli ask "?"   # One-shot query
    python -m cortex.cli agent "x" # Autonomous agent
"""

# Module ownership: CLI entry point and command routing
from __future__ import annotations


def _check_cli_deps() -> bool:
    """Check if CLI dependencies are installed."""
    missing: list[str] = []
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("rich")
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        missing.append("prompt_toolkit")

    if missing:
        print(f"CLI dependencies not installed: {', '.join(missing)}")
        print("Install with: pip install atlas-cortex[cli]")
        print("Or: pip install rich prompt_toolkit")
        return False
    return True
