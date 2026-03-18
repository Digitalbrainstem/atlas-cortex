"""Playback router — resolves targets and routes audio.

# Module ownership: Playback target resolution and audio routing

Priority order: satellite → chromecast → HA media_player.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from cortex.db import get_db

logger = logging.getLogger(__name__)


@dataclass
class PlaybackTarget:
    """Represents where audio plays."""

    target_type: str  # satellite, chromecast, ha_media_player
    target_id: str  # satellite_id, chromecast_name, entity_id
    room: str = ""
    metadata: dict = field(default_factory=dict)


class PlaybackRouter:
    """Resolves the best playback target for a room and routes audio."""

    def __init__(self) -> None:
        self._chromecasts: list[dict] = []

    # ── Target resolution ────────────────────────────────────────

    async def resolve_target(self, room: str) -> PlaybackTarget | None:
        """Find the best playback target for a room.

        Priority: satellite → chromecast → HA media_player.
        """
        # 1. Check for a satellite in this room
        target = self._find_satellite(room)
        if target:
            return target

        # 2. Check for a Chromecast in this room
        target = self._find_chromecast(room)
        if target:
            return target

        # 3. Fall back to HA media_player
        target = self._find_ha_player(room)
        if target:
            return target

        logger.warning("No playback target found for room: %s", room)
        return None

    def _find_satellite(self, room: str) -> PlaybackTarget | None:
        """Look up satellite devices in the DB for the given room."""
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT id, room FROM satellites WHERE room = ? AND status = 'online' "
                "ORDER BY id LIMIT 1",
                (room,),
            ).fetchone()
            if row:
                return PlaybackTarget(
                    target_type="satellite",
                    target_id=str(row["id"]) if isinstance(row, dict) else str(row[0]),
                    room=room,
                )
        except Exception as exc:
            logger.debug("Satellite lookup error: %s", exc)
        return None

    def _find_chromecast(self, room: str) -> PlaybackTarget | None:
        """Match a discovered Chromecast to the given room."""
        room_lower = room.lower()
        for cc in self._chromecasts:
            cc_name = cc.get("name", "").lower()
            cc_room = cc.get("room", "").lower()
            if room_lower in cc_name or room_lower == cc_room:
                return PlaybackTarget(
                    target_type="chromecast",
                    target_id=cc.get("name", ""),
                    room=room,
                    metadata=cc,
                )
        return None

    def _find_ha_player(self, room: str) -> PlaybackTarget | None:
        """Look up HA media_player entities for the given room."""
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT entity_id, friendly_name FROM ha_devices "
                "WHERE domain = 'media_player' AND (area_id = ? OR area_name = ?) "
                "ORDER BY entity_id LIMIT 1",
                (room, room),
            ).fetchone()
            if row:
                eid = row["entity_id"] if isinstance(row, dict) else row[0]
                return PlaybackTarget(
                    target_type="ha_media_player",
                    target_id=eid,
                    room=room,
                )
        except Exception as exc:
            logger.debug("HA media_player lookup error: %s", exc)
        return None

    # ── Playback control ─────────────────────────────────────────

    async def play(self, stream_url: str, target: PlaybackTarget) -> bool:
        """Route audio to the target."""
        if target.target_type == "satellite":
            return await self._play_satellite(stream_url, target)
        if target.target_type == "chromecast":
            return await self._play_chromecast(stream_url, target)
        if target.target_type == "ha_media_player":
            return await self._play_ha(stream_url, target)
        logger.error("Unknown target type: %s", target.target_type)
        return False

    async def stop(self, target: PlaybackTarget) -> bool:
        """Stop playback on the target."""
        logger.info("Stopping playback on %s/%s", target.target_type, target.target_id)
        if target.target_type == "chromecast":
            return await self._chromecast_command(target, "stop")
        if target.target_type == "ha_media_player":
            return await self._ha_command(target, "media_stop")
        # Satellite: stub
        logger.info("[stub] Would stop satellite %s", target.target_id)
        return True

    async def pause(self, target: PlaybackTarget) -> bool:
        """Pause playback on the target."""
        logger.info("Pausing playback on %s/%s", target.target_type, target.target_id)
        if target.target_type == "chromecast":
            return await self._chromecast_command(target, "pause")
        if target.target_type == "ha_media_player":
            return await self._ha_command(target, "media_pause")
        logger.info("[stub] Would pause satellite %s", target.target_id)
        return True

    async def set_volume(self, target: PlaybackTarget, volume: float) -> bool:
        """Set volume (0.0–1.0) on the target."""
        volume = max(0.0, min(1.0, volume))
        logger.info("Setting volume to %.0f%% on %s/%s",
                     volume * 100, target.target_type, target.target_id)
        if target.target_type == "chromecast":
            return await self._chromecast_volume(target, volume)
        if target.target_type == "ha_media_player":
            return await self._ha_volume(target, volume)
        logger.info("[stub] Would set satellite %s volume to %.1f", target.target_id, volume)
        return True

    async def transfer(self, from_room: str, to_room: str) -> bool:
        """Move playback from one room to another."""
        logger.info("Transferring playback from %s to %s", from_room, to_room)
        from_target = await self.resolve_target(from_room)
        to_target = await self.resolve_target(to_room)
        if not from_target or not to_target:
            logger.warning("Cannot transfer — missing target (from=%s, to=%s)",
                           from_target, to_target)
            return False
        # In a full implementation we'd get the current stream URL/position,
        # start it on the new target, then stop the old.
        logger.info("[stub] Would transfer from %s to %s", from_target, to_target)
        return True

    # ── Discovery ────────────────────────────────────────────────

    async def discover_chromecasts(self) -> list[dict]:
        """Discover Chromecast devices on the network using pychromecast."""
        try:
            import pychromecast  # type: ignore[import-untyped]
        except ImportError:
            logger.info("pychromecast not installed — skipping Chromecast discovery")
            return []

        try:
            services, browser = pychromecast.discovery.discover_chromecasts()
            pychromecast.discovery.stop_discovery(browser)
            results: list[dict] = []
            for svc in services:
                results.append({
                    "name": svc.friendly_name,
                    "model": svc.model_name,
                    "uuid": str(svc.uuid),
                    "host": str(svc.host),
                    "port": svc.port,
                    "room": "",
                })
            self._chromecasts = results
            logger.info("Discovered %d Chromecast(s)", len(results))
            return results
        except Exception as exc:
            logger.warning("Chromecast discovery failed: %s", exc)
            return []

    async def get_all_targets(self) -> list[PlaybackTarget]:
        """List all available playback targets (satellites + chromecasts + HA)."""
        targets: list[PlaybackTarget] = []

        # Satellites from DB
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT id, room FROM satellites WHERE status = 'online'"
            ).fetchall()
            for row in rows:
                sid = row["id"] if isinstance(row, dict) else row[0]
                area = row["room"] if isinstance(row, dict) else row[1]
                targets.append(PlaybackTarget(
                    target_type="satellite", target_id=str(sid), room=area or "",
                ))
        except Exception:
            pass

        # Chromecasts
        for cc in self._chromecasts:
            targets.append(PlaybackTarget(
                target_type="chromecast",
                target_id=cc.get("name", ""),
                room=cc.get("room", ""),
                metadata=cc,
            ))

        # HA media_players
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT entity_id, area_id, area_name FROM ha_devices "
                "WHERE domain = 'media_player'"
            ).fetchall()
            for row in rows:
                eid = row["entity_id"] if isinstance(row, dict) else row[0]
                area = (row["area_name"] if isinstance(row, dict) else row[2]) or ""
                targets.append(PlaybackTarget(
                    target_type="ha_media_player", target_id=eid, room=area,
                ))
        except Exception:
            pass

        return targets

    # ── Private transport methods ────────────────────────────────

    async def _play_satellite(self, stream_url: str, target: PlaybackTarget) -> bool:
        """Stream audio to a satellite via WebSocket (stub)."""
        logger.info(
            "[stub] Would stream %s to satellite %s (room %s) via WebSocket",
            stream_url, target.target_id, target.room,
        )
        return True

    async def _play_chromecast(self, stream_url: str, target: PlaybackTarget) -> bool:
        """Cast audio to a Chromecast device."""
        try:
            import pychromecast  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("pychromecast not installed — cannot cast")
            return False

        try:
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[target.target_id],
            )
            pychromecast.discovery.stop_discovery(browser)
            if not chromecasts:
                logger.warning("Chromecast %s not found", target.target_id)
                return False
            cast = chromecasts[0]
            cast.wait()
            mc = cast.media_controller
            mc.play_media(stream_url, "audio/mpeg")
            mc.block_until_active()
            logger.info("Playing on Chromecast %s", target.target_id)
            return True
        except Exception as exc:
            logger.error("Chromecast play error: %s", exc)
            return False

    async def _play_ha(self, stream_url: str, target: PlaybackTarget) -> bool:
        """Play via Home Assistant media_player service call."""
        import httpx

        ha_url = os.environ.get("HA_URL", "")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            logger.warning("HA_URL/HA_TOKEN not configured")
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{ha_url}/api/services/media_player/play_media",
                    headers={"Authorization": f"Bearer {ha_token}"},
                    json={
                        "entity_id": target.target_id,
                        "media_content_id": stream_url,
                        "media_content_type": "music",
                    },
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("HA media_player play error: %s", exc)
            return False

    async def _chromecast_command(self, target: PlaybackTarget, command: str) -> bool:
        """Send a control command to a Chromecast."""
        try:
            import pychromecast  # type: ignore[import-untyped]
        except ImportError:
            return False

        try:
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[target.target_id],
            )
            pychromecast.discovery.stop_discovery(browser)
            if not chromecasts:
                return False
            cast = chromecasts[0]
            cast.wait()
            mc = cast.media_controller
            if command == "stop":
                mc.stop()
            elif command == "pause":
                mc.pause()
            return True
        except Exception as exc:
            logger.error("Chromecast %s error: %s", command, exc)
            return False

    async def _chromecast_volume(self, target: PlaybackTarget, volume: float) -> bool:
        """Set Chromecast volume."""
        try:
            import pychromecast  # type: ignore[import-untyped]
        except ImportError:
            return False

        try:
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[target.target_id],
            )
            pychromecast.discovery.stop_discovery(browser)
            if not chromecasts:
                return False
            cast = chromecasts[0]
            cast.wait()
            cast.set_volume(volume)
            return True
        except Exception as exc:
            logger.error("Chromecast volume error: %s", exc)
            return False

    async def _ha_command(self, target: PlaybackTarget, service: str) -> bool:
        """Call an HA media_player service."""
        import httpx

        ha_url = os.environ.get("HA_URL", "")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{ha_url}/api/services/media_player/{service}",
                    headers={"Authorization": f"Bearer {ha_token}"},
                    json={"entity_id": target.target_id},
                )
                resp.raise_for_status()
                return True
        except Exception:
            return False

    async def _ha_volume(self, target: PlaybackTarget, volume: float) -> bool:
        """Set HA media_player volume."""
        import httpx

        ha_url = os.environ.get("HA_URL", "")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{ha_url}/api/services/media_player/volume_set",
                    headers={"Authorization": f"Bearer {ha_token}"},
                    json={"entity_id": target.target_id, "volume_level": volume},
                )
                resp.raise_for_status()
                return True
        except Exception:
            return False
