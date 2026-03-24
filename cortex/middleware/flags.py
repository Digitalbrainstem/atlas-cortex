"""Experiment flags for middleware slots.

Each experiment slot (EXP_001 through EXP_010) can be toggled via:
  - Environment variable: ATLAS_EXP_001=1
  - Database table: experiment_flags

Flags are OFF by default. Config values can be stored as JSON
in the database for runtime parameters.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MAX_SLOTS = 10


def _env_key(slot: int) -> str:
    return f"ATLAS_EXP_{slot:03d}"


def is_experiment_enabled(slot: int) -> bool:
    """Check if experiment slot is enabled.

    Checks environment first, then database.  Any truthy value
    (``"1"``, ``"true"``, ``"yes"``) enables the slot.
    """
    if slot < 1 or slot > MAX_SLOTS:
        return False

    # Environment takes priority
    env_val = os.environ.get(_env_key(slot), "").strip().lower()
    if env_val in ("1", "true", "yes", "on"):
        return True
    if env_val in ("0", "false", "no", "off"):
        return False

    # Fall back to database
    try:
        from cortex.db import get_db
        db = get_db()
        row = db.execute(
            "SELECT enabled FROM experiment_flags WHERE slot = ?", (slot,)
        ).fetchone()
        if row:
            return bool(row[0])
    except Exception:
        pass

    return False


def get_experiment_config(slot: int) -> dict[str, Any]:
    """Get JSON config for an experiment slot.

    Returns an empty dict if the slot has no config or is disabled.
    """
    if not is_experiment_enabled(slot):
        return {}

    # Check environment: ATLAS_EXP_001_CONFIG='{"key":"val"}'
    env_cfg = os.environ.get(f"{_env_key(slot)}_CONFIG", "")
    if env_cfg:
        try:
            return json.loads(env_cfg)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in %s_CONFIG", _env_key(slot))

    # Fall back to database
    try:
        from cortex.db import get_db
        db = get_db()
        row = db.execute(
            "SELECT config_json FROM experiment_flags WHERE slot = ?", (slot,)
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass

    return {}


def set_experiment_flag(slot: int, enabled: bool,
                        config: dict[str, Any] | None = None) -> None:
    """Set experiment flag in the database."""
    if slot < 1 or slot > MAX_SLOTS:
        raise ValueError(f"Slot must be 1-{MAX_SLOTS}, got {slot}")

    try:
        from cortex.db import get_db
        db = get_db()
        db.execute(
            """INSERT INTO experiment_flags (slot, enabled, config_json)
               VALUES (?, ?, ?)
               ON CONFLICT(slot) DO UPDATE
               SET enabled = excluded.enabled,
                   config_json = excluded.config_json""",
            (slot, int(enabled), json.dumps(config) if config else None),
        )
        db.commit()
    except Exception as e:
        logger.error("Failed to set experiment flag %d: %s", slot, e)


def init_experiment_table() -> None:
    """Create the experiment_flags table if it doesn't exist."""
    try:
        from cortex.db import get_db
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS experiment_flags (
                slot INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                config_json TEXT,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
    except Exception as e:
        logger.debug("Could not init experiment_flags table: %s", e)
