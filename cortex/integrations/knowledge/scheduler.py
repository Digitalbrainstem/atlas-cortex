"""Knowledge sync scheduler — periodic background sync for all sources (Phase I5.5)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeSyncScheduler:
    """Runs periodic background sync for all configured knowledge sources."""

    def __init__(self, conn: sqlite3.Connection, interval_minutes: int = 60) -> None:
        self.conn = conn
        self.interval = interval_minutes
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_sync: str | None = None

    async def start(self) -> None:
        """Start the background sync loop."""
        if self._running:
            logger.warning("Sync scheduler is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Knowledge sync scheduler started (interval=%d min)", self.interval)

    async def stop(self) -> None:
        """Stop the background sync loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Knowledge sync scheduler stopped")

    async def run_sync(self) -> dict:
        """Run a single sync cycle across all configured sources.

        Reads source configs from the ``discovered_services`` table.

        Returns: ``{sources_synced, total_files, total_events, errors}``
        """
        stats = {
            "sources_synced": 0,
            "total_files": 0,
            "total_events": 0,
            "errors": 0,
        }

        services = self._get_configured_services()

        for service in services:
            service_type = service["service_type"]
            url = service["url"]
            service_id = service["id"]

            try:
                config = self._get_service_config(service_id)

                if service_type == "webdav":
                    result = await self._sync_webdav(url, config)
                    stats["total_files"] += result.get("files_new", 0) + result.get(
                        "files_updated", 0
                    )
                elif service_type == "caldav":
                    result = await self._sync_caldav(url, config)
                    stats["total_events"] += result.get("events_synced", 0)
                else:
                    logger.debug("Unknown knowledge source type: %s", service_type)
                    continue

                stats["sources_synced"] += 1
                self._update_health(service_id, "healthy")

            except Exception as exc:
                logger.error("Sync error for %s (%s): %s", service_type, url, exc)
                stats["errors"] += 1
                self._update_health(service_id, "error")

        self._last_sync = datetime.now(timezone.utc).isoformat()
        return stats

    @property
    def running(self) -> bool:
        """Whether the sync loop is currently active."""
        return self._running

    @property
    def last_sync(self) -> str | None:
        """ISO timestamp of the last completed sync cycle, or None."""
        return self._last_sync

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        """Main background loop — runs sync then sleeps."""
        while self._running:
            try:
                result = await self.run_sync()
                logger.info(
                    "Sync cycle complete: %d sources, %d files, %d events, %d errors",
                    result["sources_synced"],
                    result["total_files"],
                    result["total_events"],
                    result["errors"],
                )
            except Exception as exc:
                logger.error("Sync cycle failed: %s", exc)

            # Sleep in small increments so stop() is responsive
            for _ in range(self.interval * 60):
                if not self._running:
                    return
                await asyncio.sleep(1)

    def _get_configured_services(self) -> list[dict]:
        """Fetch active knowledge sources from discovered_services."""
        try:
            rows = self.conn.execute(
                "SELECT id, service_type, url FROM discovered_services "
                "WHERE is_configured = TRUE AND is_active = TRUE "
                "AND service_type IN ('webdav', 'caldav')"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Failed to read discovered_services: %s", exc)
            return []

    def _get_service_config(self, service_id: int) -> dict:
        """Read config key-value pairs for a service."""
        try:
            rows = self.conn.execute(
                "SELECT config_key, config_value FROM service_config WHERE service_id = ?",
                (service_id,),
            ).fetchall()
            return {r["config_key"]: r["config_value"] for r in rows}
        except Exception:
            return {}

    def _update_health(self, service_id: int, status: str) -> None:
        """Update the health status of a service."""
        try:
            self.conn.execute(
                "UPDATE discovered_services SET health_status = ?, "
                "last_health_check = CURRENT_TIMESTAMP WHERE id = ?",
                (status, service_id),
            )
            self.conn.commit()
        except Exception as exc:
            logger.debug("Failed to update health for service %d: %s", service_id, exc)

    async def _sync_webdav(self, url: str, config: dict) -> dict:
        """Sync a single WebDAV source."""
        from cortex.integrations.knowledge.webdav import WebDAVConnector

        connector = WebDAVConnector(
            url=url,
            username=config.get("username", ""),
            password=config.get("password", ""),
            remote_path=config.get("remote_path", "/"),
        )
        try:
            return await connector.sync(self.conn, owner_id=config.get("owner_id", "system"))
        finally:
            await connector.aclose()

    async def _sync_caldav(self, url: str, config: dict) -> dict:
        """Sync a single CalDAV source."""
        from cortex.integrations.knowledge.caldav import CalDAVConnector

        connector = CalDAVConnector(
            url=url,
            username=config.get("username", ""),
            password=config.get("password", ""),
        )
        try:
            return await connector.sync_to_knowledge(
                self.conn, owner_id=config.get("owner_id", "system")
            )
        finally:
            await connector.aclose()
