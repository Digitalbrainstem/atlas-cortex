"""Self-modification zones — defines what the learning system can change.

THIS FILE IS FROZEN. Do not modify without explicit human approval.

Zones classify every module as FROZEN (safety-critical, never auto-modified)
or MUTABLE (can be updated by the learning/evolution system).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Zone(Enum):
    FROZEN = "frozen"      # Safety-critical — human review required
    MUTABLE = "mutable"    # Learning system may propose changes
    GENERATED = "generated"  # Fully auto-generated (patterns, caches)


@dataclass(frozen=True)
class ZoneRule:
    """Maps a path pattern to a modification zone."""
    pattern: str  # glob-style path pattern relative to cortex/
    zone: Zone
    reason: str


# ── Zone definitions ─────────────────────────────────────────────
# Order matters: first match wins.

_ZONE_RULES: list[ZoneRule] = [
    # FROZEN: Safety system — never auto-modified
    ZoneRule("safety/*", Zone.FROZEN, "Safety guardrails are human-reviewed only"),
    ZoneRule("selfmod/*", Zone.FROZEN, "Self-modification rules are human-reviewed only"),
    ZoneRule("auth.py", Zone.FROZEN, "Authentication is security-critical"),
    ZoneRule("db.py", Zone.FROZEN, "Database schema changes require migration planning"),

    # FROZEN: Core pipeline structure
    ZoneRule("pipeline/__init__.py", Zone.FROZEN, "Pipeline orchestration is stability-critical"),
    ZoneRule("pipeline/events.py", Zone.FROZEN, "Event types are API contract"),

    # MUTABLE: Pipeline layers can learn new patterns
    ZoneRule("pipeline/layer1_instant.py", Zone.MUTABLE, "Instant answers can learn new patterns"),
    ZoneRule("pipeline/layer2_plugins.py", Zone.MUTABLE, "Plugin dispatch can learn new routes"),

    # GENERATED: Fully auto-managed
    ZoneRule("integrations/learning/*", Zone.MUTABLE, "Learning system manages its own patterns"),

    # MUTABLE: Content and filler
    ZoneRule("content/*", Zone.MUTABLE, "Content can be auto-generated"),
    ZoneRule("filler/*", Zone.MUTABLE, "Filler phrases can be tuned"),

    # FROZEN: Everything else by default
    ZoneRule("*", Zone.FROZEN, "Default: require human review"),
]

# Convenience sets for quick lookup
FROZEN_ZONES = {r.pattern for r in _ZONE_RULES if r.zone == Zone.FROZEN}
MUTABLE_ZONES = {r.pattern for r in _ZONE_RULES if r.zone == Zone.MUTABLE}


def get_zone(path: str) -> ZoneRule:
    """Return the zone rule for a given path (relative to cortex/)."""
    import fnmatch
    # Strip leading cortex/ if present
    if path.startswith("cortex/"):
        path = path[len("cortex/"):]
    for rule in _ZONE_RULES:
        if fnmatch.fnmatch(path, rule.pattern):
            return rule
    return _ZONE_RULES[-1]  # default: frozen


def validate_change(path: str) -> tuple[bool, str]:
    """Check if a path can be auto-modified.

    Returns (allowed, reason).
    """
    rule = get_zone(path)
    allowed = rule.zone in (Zone.MUTABLE, Zone.GENERATED)
    return allowed, rule.reason
