"""Routines & Automations engine for Atlas Cortex."""
# Module ownership: Routine automation engine and action executors

from __future__ import annotations

from cortex.routines.engine import RoutineEngine
from cortex.routines.actions import (
    ActionExecutor,
    ActionResult,
    TTSAnnounceAction,
    HAServiceAction,
    DelayAction,
    ConditionAction,
    SetVariableAction,
)
from cortex.routines.templates import TEMPLATES, instantiate_template
from cortex.routines.triggers import TriggerManager

__all__ = [
    "RoutineEngine",
    "TriggerManager",
    "ActionExecutor",
    "ActionResult",
    "TEMPLATES",
    "instantiate_template",
    "TTSAnnounceAction",
    "HAServiceAction",
    "DelayAction",
    "ConditionAction",
    "SetVariableAction",
]
