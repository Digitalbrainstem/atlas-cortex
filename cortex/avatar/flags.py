"""Avatar feature flag resolution.

Three-layer visibility model:
  Normal   — all flags OFF (clean face)
  User     — per-user overrides enabled by admin
  Dev/Debug — dev_mode forces ALL flags ON
"""

# Module ownership: Avatar feature flag resolution

from __future__ import annotations

import logging

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)

KNOWN_FLAGS = [
    "show_mic",
    "show_skin_switcher",
    "show_joke_button",
    "show_controls",
    "show_debug",
    "satellite_mode",
    "dev_mode",
]


def get_avatar_config(user_id: str = "") -> dict[str, bool]:
    """Resolve feature flags for a user.

    Resolution order:
    1. Start with all flags OFF
    2. Apply global defaults
    3. Apply per-user overrides (if user_id provided)
    4. If dev_mode is ON, force ALL flags ON
    """
    init_db()
    conn = get_db()

    config: dict[str, bool] = {f: False for f in KNOWN_FLAGS}

    # Global defaults
    rows = conn.execute(
        "SELECT flag_name, enabled FROM avatar_feature_flags WHERE scope = 'global'",
    ).fetchall()
    for row in rows:
        name = row[0] if isinstance(row, (tuple, list)) else row["flag_name"]
        val = row[1] if isinstance(row, (tuple, list)) else row["enabled"]
        if name in config:
            config[name] = bool(val)

    # Per-user overrides
    if user_id:
        rows = conn.execute(
            "SELECT flag_name, enabled FROM avatar_feature_flags WHERE scope = ?",
            (user_id,),
        ).fetchall()
        for row in rows:
            name = row[0] if isinstance(row, (tuple, list)) else row["flag_name"]
            val = row[1] if isinstance(row, (tuple, list)) else row["enabled"]
            if name in config:
                config[name] = bool(val)

    # Dev mode forces everything ON
    if config.get("dev_mode"):
        for flag in KNOWN_FLAGS:
            config[flag] = True

    return config


def set_flag(scope: str, flag_name: str, enabled: bool) -> None:
    """Set a flag. scope='global' or a user_id."""
    if flag_name not in KNOWN_FLAGS:
        return
    init_db()
    conn = get_db()
    conn.execute(
        "INSERT INTO avatar_feature_flags (scope, flag_name, enabled) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(scope, flag_name) DO UPDATE SET enabled = excluded.enabled",
        (scope, flag_name, int(enabled)),
    )
    conn.commit()


def get_all_flags() -> dict[str, dict[str, bool]]:
    """Get all flags grouped by scope. For admin UI."""
    init_db()
    conn = get_db()
    rows = conn.execute(
        "SELECT scope, flag_name, enabled FROM avatar_feature_flags ORDER BY scope, flag_name",
    ).fetchall()
    result: dict[str, dict[str, bool]] = {}
    for row in rows:
        scope = row[0] if isinstance(row, (tuple, list)) else row["scope"]
        name = row[1] if isinstance(row, (tuple, list)) else row["flag_name"]
        val = row[2] if isinstance(row, (tuple, list)) else row["enabled"]
        if scope not in result:
            result[scope] = {}
        result[scope][name] = bool(val)
    return result


def reset_flags(scope: str = "global") -> None:
    """Reset all flags for a scope to defaults (all OFF)."""
    init_db()
    conn = get_db()
    if scope == "global":
        conn.execute(
            "UPDATE avatar_feature_flags SET enabled = 0 WHERE scope = 'global'",
        )
    else:
        conn.execute(
            "DELETE FROM avatar_feature_flags WHERE scope = ?",
            (scope,),
        )
    conn.commit()
