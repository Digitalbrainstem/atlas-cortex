"""Service registry — persists discovered/configured services to SQLite."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from cortex.discovery.scanner import DiscoveredService

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """CRUD wrapper around the ``discovered_services`` / ``service_config`` tables.

    Args:
        conn: An open :class:`sqlite3.Connection` (WAL mode recommended).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ #
    # Services                                                             #
    # ------------------------------------------------------------------ #

    def upsert_service(self, svc: DiscoveredService) -> int:
        """Insert or update a discovered service row.

        Uniqueness key: ``(service_type, url)``.

        Returns:
            The ``id`` of the inserted/updated row.
        """
        cur = self._conn.execute(
            """
            INSERT INTO discovered_services
                (service_type, name, url, discovery_method,
                 is_configured, is_active, health_status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(service_type, url) DO UPDATE SET
                name             = excluded.name,
                discovery_method = excluded.discovery_method,
                health_status    = excluded.health_status,
                updated_at       = CURRENT_TIMESTAMP
            """,
            (
                svc.service_type,
                svc.name,
                svc.url,
                svc.discovery_method,
                svc.is_configured,
                svc.is_active,
                svc.health_status,
            ),
        )
        self._conn.commit()
        row_id: int = cur.lastrowid or self._id_for(svc.service_type, svc.url)
        logger.debug("upsert_service id=%d type=%s url=%s", row_id, svc.service_type, svc.url)
        return row_id

    def mark_active(self, service_id: int, active: bool = True) -> None:
        """Set ``is_active`` for *service_id*."""
        self._conn.execute(
            "UPDATE discovered_services SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (active, service_id),
        )
        self._conn.commit()

    def health_check_update(self, service_id: int, status: str) -> None:
        """Record the latest health-check result for *service_id*."""
        self._conn.execute(
            """
            UPDATE discovered_services
               SET health_status = ?, last_health_check = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (status, service_id),
        )
        self._conn.commit()

    def list_services(
        self,
        service_type: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return service rows, optionally filtered.

        Args:
            service_type: If given, only return rows of this type.
            active_only:  If ``True``, only return ``is_active = 1`` rows.

        Returns:
            List of dicts (one per row).
        """
        params: list[Any] = []
        # Build query with explicit branches to avoid f-string SQL construction
        if service_type and active_only:
            sql = "SELECT * FROM discovered_services WHERE service_type = ? AND is_active = 1 ORDER BY id"
            params = [service_type]
        elif service_type:
            sql = "SELECT * FROM discovered_services WHERE service_type = ? ORDER BY id"
            params = [service_type]
        elif active_only:
            sql = "SELECT * FROM discovered_services WHERE is_active = 1 ORDER BY id"
        else:
            sql = "SELECT * FROM discovered_services ORDER BY id"
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def get_active_service(self, service_type: str) -> dict[str, Any] | None:
        """Return the first active service of *service_type*, or ``None``."""
        cur = self._conn.execute(
            "SELECT * FROM discovered_services WHERE service_type = ? AND is_active = 1 LIMIT 1",
            (service_type,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Config                                                               #
    # ------------------------------------------------------------------ #

    def set_config(
        self,
        service_id: int,
        key: str,
        value: str,
        sensitive: bool = False,
    ) -> None:
        """Insert or replace a config key/value pair for *service_id*.

        Args:
            service_id: FK into ``discovered_services``.
            key:        Config key (e.g. ``"token"``, ``"base_url"``).
            value:      Config value stored as plain text.  For production
                        deployments, callers should encrypt secrets before
                        passing them here and decrypt on retrieval.
            sensitive:  If ``True``, marks the row ``is_sensitive = 1`` so that
                        tools reading the DB can redact the value in logs/UI.
        """
        self._conn.execute(
            """
            INSERT INTO service_config (service_id, config_key, config_value, is_sensitive, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(service_id, config_key) DO UPDATE SET
                config_value = excluded.config_value,
                is_sensitive = excluded.is_sensitive,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (service_id, key, value, sensitive),
        )
        self._conn.execute(
            "UPDATE discovered_services SET is_configured = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (service_id,),
        )
        self._conn.commit()

    def get_config(self, service_id: int) -> dict[str, str]:
        """Return all config entries for *service_id* as ``{key: value}``."""
        cur = self._conn.execute(
            "SELECT config_key, config_value FROM service_config WHERE service_id = ?",
            (service_id,),
        )
        return {row["config_key"]: row["config_value"] for row in cur.fetchall()}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _id_for(self, service_type: str, url: str) -> int:
        cur = self._conn.execute(
            "SELECT id FROM discovered_services WHERE service_type = ? AND url = ?",
            (service_type, url),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Service not found: {service_type} @ {url}")
        return int(row["id"])
