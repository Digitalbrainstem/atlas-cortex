"""Voice-accessible backup commands for Atlas Cortex (Phase I7.2).

Provides natural-language handlers for backup operations that can be
wired into the Layer 2 plugin dispatch.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Intent patterns ───────────────────────────────────────────────

_BACKUP_NOW = re.compile(
    r"\b(?:backup|back\s*up)\b.*\b(?:now|immediately|today|please)\b",
    re.I,
)
_LAST_BACKUP = re.compile(
    r"\b(?:when|what\s*time).*\b(?:last|latest|recent)\s*(?:backup|back\s*up)\b",
    re.I,
)
_LIST_BACKUPS = re.compile(
    r"\b(?:list|show|how\s*many)\b.*\b(?:backups?|back\s*ups?)\b",
    re.I,
)


def match_backup_intent(message: str) -> str | None:
    """Return a backup intent string if the message is backup-related.

    Returns:
        ``"create"``, ``"last"``, ``"list"``, or ``None``.
    """
    if _BACKUP_NOW.search(message):
        return "create"
    if _LAST_BACKUP.search(message):
        return "last"
    if _LIST_BACKUPS.search(message):
        return "list"
    return None


async def handle_backup_command(
    intent: str,
    conn: Any = None,
    offsite: Any = None,
) -> str:
    """Execute a backup voice command and return a natural language response.

    Args:
        intent:  One of ``"create"``, ``"last"``, ``"list"``.
        conn:    SQLite connection for reading backup_log.
        offsite: Optional :class:`~cortex.backup.offsite.OffsiteBackup` for NAS sync.
    """
    if intent == "create":
        return await _handle_create(conn, offsite)
    if intent == "last":
        return _handle_last(conn)
    if intent == "list":
        return _handle_list(conn)
    return "I'm not sure what you want me to do with backups."


async def _handle_create(conn: Any, offsite: Any) -> str:
    """Create a backup and optionally sync to NAS."""
    try:
        from cortex.backup import create_backup
        result = create_backup()
        response = f"Backup created: {result['archive_name']}"

        if offsite is not None:
            try:
                sync_result = await offsite.sync()
                response += f" and synced to NAS ({sync_result.get('files_synced', 0)} files)."
            except Exception as exc:
                response += f". NAS sync failed: {exc}"
        else:
            response += "."

        return response
    except Exception as exc:
        logger.error("Backup creation failed: %s", exc)
        return f"Backup failed: {exc}"


def _handle_last(conn: Any) -> str:
    """Report when the last backup was made."""
    if conn is None:
        return "I can't check — no database connection."
    try:
        row = conn.execute(
            "SELECT archive_path, created_at, success FROM backup_log "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "No backups have been made yet."
        name = Path(row["archive_path"]).name
        status = "successful" if row["success"] else "failed"
        return f"The last backup was {name} on {row['created_at']} ({status})."
    except Exception as exc:
        logger.error("Backup query failed: %s", exc)
        return f"Error checking backup history: {exc}"


def _handle_list(conn: Any) -> str:
    """List recent backups."""
    if conn is None:
        return "I can't check — no database connection."
    try:
        rows = conn.execute(
            "SELECT archive_path, created_at, size_bytes, success FROM backup_log "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        if not rows:
            return "No backups found."
        lines = [f"Last {len(rows)} backups:"]
        for r in rows:
            name = Path(r["archive_path"]).name
            size_mb = (r["size_bytes"] or 0) / (1024 * 1024)
            status = "✓" if r["success"] else "✗"
            lines.append(f"  {status} {name} ({size_mb:.1f} MB)")
        return "\n".join(lines)
    except Exception as exc:
        logger.error("Backup list failed: %s", exc)
        return f"Error listing backups: {exc}"
