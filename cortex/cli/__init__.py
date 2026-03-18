"""Atlas CLI — Terminal-based AI assistant with chat and agent modes.

Usage::

    python -m cortex.cli chat      # Interactive chat
    python -m cortex.cli ask "?"   # One-shot query
    python -m cortex.cli agent "x" # Autonomous agent
"""

# Module ownership: CLI entry point and command routing
from __future__ import annotations
