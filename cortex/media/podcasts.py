"""Podcast provider — subscribe, fetch episodes, track progress.

# Module ownership: Podcast RSS feed management and episode playback
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from cortex.db import get_db
from cortex.media.base import MediaItem, MediaProvider

logger = logging.getLogger(__name__)


class PodcastProvider(MediaProvider):
    """Provider for podcast RSS feeds."""

    provider_id = "podcasts"
    display_name = "Podcasts"
    supports_streaming = True

    # ── Subscription management ──────────────────────────────────

    async def subscribe(self, feed_url: str) -> int:
        """Subscribe to a podcast RSS feed. Returns subscription_id."""
        title, description = await self._fetch_feed_info(feed_url)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO podcast_subscriptions (title, feed_url, description) "
                "VALUES (?, ?, ?)",
                (title, feed_url, description),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM podcast_subscriptions WHERE feed_url = ?",
                (feed_url,),
            ).fetchone()
            sub_id = row[0] if row else 0
            # Fetch initial episodes
            await self._refresh_feed(sub_id, feed_url)
            logger.info("Subscribed to podcast: %s (id=%d)", title, sub_id)
            return sub_id
        except Exception as exc:
            logger.error("Subscribe failed: %s", exc)
            return 0

    async def unsubscribe(self, subscription_id: int) -> bool:
        """Remove a podcast subscription and its episodes."""
        conn = get_db()
        conn.execute(
            "DELETE FROM podcast_subscriptions WHERE id = ?",
            (subscription_id,),
        )
        conn.commit()
        return True

    async def list_subscriptions(self) -> list[dict]:
        """Return all podcast subscriptions."""
        conn = get_db()
        rows = conn.execute(
            "SELECT id, title, feed_url, description, last_checked, auto_download "
            "FROM podcast_subscriptions ORDER BY title"
        ).fetchall()
        return [
            {
                "id": r[0],
                "title": r[1],
                "feed_url": r[2],
                "description": r[3],
                "last_checked": r[4],
                "auto_download": bool(r[5]),
            }
            for r in rows
        ]

    # ── Episode management ───────────────────────────────────────

    async def check_new_episodes(self) -> list[dict]:
        """Check all feeds for new episodes. Returns new episodes found."""
        conn = get_db()
        subs = conn.execute(
            "SELECT id, feed_url FROM podcast_subscriptions"
        ).fetchall()
        new_episodes: list[dict] = []
        for sub in subs:
            sub_id, feed_url = sub[0], sub[1]
            try:
                found = await self._refresh_feed(sub_id, feed_url)
                new_episodes.extend(found)
            except Exception as exc:
                logger.warning("Failed to check feed %s: %s", feed_url, exc)
        return new_episodes

    async def get_episodes(self, subscription_id: int) -> list[MediaItem]:
        """Get episodes for a subscription."""
        conn = get_db()
        rows = conn.execute(
            "SELECT id, title, audio_url, description, duration_seconds, "
            "published_at, listened, progress_seconds "
            "FROM podcast_episodes WHERE subscription_id = ? "
            "ORDER BY published_at DESC",
            (subscription_id,),
        ).fetchall()
        return [
            MediaItem(
                id=str(r[0]),
                title=r[1] or "",
                provider=self.provider_id,
                media_type="podcast",
                stream_url=r[2] or "",
                duration_seconds=r[4] or 0,
                metadata={
                    "description": r[3] or "",
                    "published_at": r[5] or "",
                    "listened": bool(r[6]),
                    "progress_seconds": r[7] or 0,
                },
            )
            for r in rows
        ]

    # ── Search ───────────────────────────────────────────────────

    async def search(self, query: str, media_type: str = "") -> list[MediaItem]:
        """Search across subscribed podcast episodes."""
        conn = get_db()
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT e.id, e.title, e.audio_url, e.description, e.duration_seconds, "
            "e.published_at, e.listened, e.progress_seconds, s.title as podcast_title "
            "FROM podcast_episodes e "
            "JOIN podcast_subscriptions s ON e.subscription_id = s.id "
            "WHERE e.title LIKE ? OR e.description LIKE ? OR s.title LIKE ? "
            "ORDER BY e.published_at DESC LIMIT 20",
            (like, like, like),
        ).fetchall()

        return [
            MediaItem(
                id=str(r[0]),
                title=r[1] or "",
                artist=r[8] or "",  # podcast title as artist
                provider=self.provider_id,
                media_type="podcast",
                stream_url=r[2] or "",
                duration_seconds=r[4] or 0,
            )
            for r in rows
        ]

    # ── Stream URL ───────────────────────────────────────────────

    async def get_stream_url(self, episode_id: str) -> str | None:
        """Return audio URL for an episode."""
        conn = get_db()
        row = conn.execute(
            "SELECT audio_url FROM podcast_episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        return row[0] if row else None

    # ── Progress tracking ────────────────────────────────────────

    async def get_progress(self, episode_id: str) -> float:
        """Get resume position in seconds."""
        conn = get_db()
        row = conn.execute(
            "SELECT progress_seconds FROM podcast_episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    async def report_progress(self, episode_id: str, position_seconds: float) -> bool:
        """Update listening position for an episode."""
        conn = get_db()
        conn.execute(
            "UPDATE podcast_episodes SET progress_seconds = ?, listened = CASE WHEN "
            "duration_seconds > 0 AND ? / duration_seconds > 0.9 THEN 1 ELSE listened END "
            "WHERE id = ?",
            (position_seconds, position_seconds, episode_id),
        )
        conn.commit()
        return True

    # ── Health ───────────────────────────────────────────────────

    async def health(self) -> bool:
        """Podcasts are always available (RSS is stdlib)."""
        return True

    # ── Private helpers ──────────────────────────────────────────

    async def _fetch_feed_info(self, feed_url: str) -> tuple[str, str]:
        """Fetch podcast title and description from RSS feed."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return ("Unknown Podcast", "")
            title = channel.findtext("title", "Unknown Podcast")
            desc = channel.findtext("description", "")
            return (title, desc)
        except Exception as exc:
            logger.warning("Failed to fetch feed info for %s: %s", feed_url, exc)
            return ("Unknown Podcast", "")

    async def _refresh_feed(self, sub_id: int, feed_url: str) -> list[dict]:
        """Fetch RSS feed and insert any new episodes."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Feed fetch failed for %s: %s", feed_url, exc)
            return []

        conn = get_db()
        conn.execute(
            "UPDATE podcast_subscriptions SET last_checked = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (sub_id,),
        )

        new_episodes: list[dict] = []
        try:
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                conn.commit()
                return []

            for item in channel.findall("item"):
                title = item.findtext("title", "")
                enclosure = item.find("enclosure")
                audio_url = enclosure.get("url", "") if enclosure is not None else ""
                if not audio_url:
                    continue

                # Skip if already exists
                existing = conn.execute(
                    "SELECT id FROM podcast_episodes "
                    "WHERE subscription_id = ? AND audio_url = ?",
                    (sub_id, audio_url),
                ).fetchone()
                if existing:
                    continue

                desc = item.findtext("description", "")
                pub_date = item.findtext("pubDate", "")
                duration_text = item.findtext(
                    "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration", "0",
                )
                duration = self._parse_duration(duration_text)

                conn.execute(
                    "INSERT INTO podcast_episodes "
                    "(subscription_id, title, audio_url, description, "
                    " duration_seconds, published_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sub_id, title, audio_url, desc, duration, pub_date),
                )
                new_episodes.append({"title": title, "subscription_id": sub_id})
        except ET.ParseError as exc:
            logger.warning("RSS parse error for %s: %s", feed_url, exc)

        conn.commit()
        return new_episodes

    @staticmethod
    def _parse_duration(text: str) -> float:
        """Parse duration from iTunes format (HH:MM:SS or seconds)."""
        if not text:
            return 0
        try:
            parts = text.strip().split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return float(parts[0])
        except (ValueError, IndexError):
            return 0
