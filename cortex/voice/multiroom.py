"""Multi-room command expansion for Atlas Cortex (Phase I3.4).

Parses spatial keywords like "downstairs", "upstairs", "everywhere",
"all rooms" from user messages and expands them into lists of target
area IDs using the :class:`~cortex.voice.spatial.SpatialEngine`.
"""

from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Spatial keywords → expansion strategy ─────────────────────────

_FLOOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:downstairs|first\s*floor|ground\s*floor|main\s*floor)\b", re.I), "ground"),
    (re.compile(r"\b(?:upstairs|second\s*floor|upper\s*floor)\b", re.I), "upper"),
    (re.compile(r"\b(?:basement|lower\s*level)\b", re.I), "basement"),
    (re.compile(r"\b(?:third\s*floor|attic)\b", re.I), "attic"),
]

_ALL_PATTERN = re.compile(
    r"\b(?:everywhere|every\s*room|all\s*rooms|the\s*(?:whole|entire)\s*house|whole\s*house)\b",
    re.I,
)

_AREA_PATTERN = re.compile(
    r"\b(?:in\s+(?:the\s+)?|the\s+)?([\w\s]+?)(?:\s+(?:lights?|fan|switch|thermostat))\b",
    re.I,
)


def extract_spatial_scope(message: str) -> dict[str, Any]:
    """Parse a message for spatial scope keywords.

    Returns a dict with:
        ``scope``: one of ``"all"``, ``"floor"``, ``"area"``, or ``None``
        ``floor``: floor name when scope is ``"floor"``
        ``area_hint``: area name substring when scope is ``"area"``
    """
    # Check "everywhere / all rooms"
    if _ALL_PATTERN.search(message):
        return {"scope": "all", "floor": None, "area_hint": None}

    # Check floor keywords
    for pattern, floor in _FLOOR_PATTERNS:
        if pattern.search(message):
            return {"scope": "floor", "floor": floor, "area_hint": None}

    return {"scope": None, "floor": None, "area_hint": None}


def expand_targets(
    message: str,
    spatial_engine: Any,
    ha_client: Any | None = None,
) -> list[str]:
    """Expand a message's spatial scope into a list of target area_ids.

    Args:
        message:        The user's raw message.
        spatial_engine: A :class:`~cortex.voice.spatial.SpatialEngine` instance.
        ha_client:      Optional HA client (unused currently, reserved for future).

    Returns:
        A list of ``area_id`` strings that should be targeted.  Returns ``[]``
        if no spatial expansion is needed (single-room command).
    """
    scope = extract_spatial_scope(message)

    if scope["scope"] == "all":
        areas = spatial_engine.expand_all_areas()
        logger.info("Multi-room expansion: ALL → %d areas", len(areas))
        return areas

    if scope["scope"] == "floor":
        areas = spatial_engine.expand_floor_areas(scope["floor"])
        logger.info("Multi-room expansion: floor=%s → %d areas", scope["floor"], len(areas))
        return areas

    return []


def build_multi_room_response(
    action: str,
    areas: list[str],
    successes: int,
    failures: int,
) -> str:
    """Build a user-facing response for a multi-room command.

    Args:
        action:    What was done (e.g. "turned off the lights").
        areas:     List of area names targeted.
        successes: How many areas succeeded.
        failures:  How many areas failed.
    """
    total = successes + failures
    if failures == 0:
        if total == 1:
            return f"Done — {action} in {areas[0]}."
        return f"Done — {action} in {total} rooms."
    return f"{action.capitalize()} in {successes} of {total} rooms ({failures} failed)."
