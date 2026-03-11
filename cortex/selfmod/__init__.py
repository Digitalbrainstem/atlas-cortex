"""Self-modification system — controlled code evolution.

Defines which parts of the codebase can be modified by the learning
system and which are frozen (safety-critical).

Sub-modules:
  zones — FROZEN/MUTABLE zone definitions
"""
from __future__ import annotations

from cortex.selfmod.zones import (
    Zone,
    FROZEN_ZONES,
    MUTABLE_ZONES,
    get_zone,
    validate_change,
)

__all__ = ["Zone", "FROZEN_ZONES", "MUTABLE_ZONES", "get_zone", "validate_change"]
