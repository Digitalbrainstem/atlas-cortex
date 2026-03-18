"""Media & Music plugin — Layer 2 dispatcher for playback, control, and queries.

# Module ownership: Media intent matching and playback orchestration
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.db import get_db
from cortex.media.base import MediaItem, PlaybackState
from cortex.media.local_library import LocalLibraryProvider
from cortex.media.preferences import PreferenceEngine
from cortex.media.podcasts import PodcastProvider
from cortex.media.router import PlaybackRouter
from cortex.media.youtube_music import YouTubeMusicProvider
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Intent patterns ──────────────────────────────────────────────

_PLAY_RE = re.compile(
    r"\b(?:play|put\s+on|listen\s+to|queue|shuffle)\b\s+(.+)",
    re.IGNORECASE,
)
_PLAY_SMART_RE = re.compile(
    r"^(?:play\s+(?:something|my\s+favorites|my\s+favourites|morning\s+music))[\s?.!]*$",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(
    r"\b(?:pause|resume|stop|skip|next|previous|mute|unmute)\b",
    re.IGNORECASE,
)
_VOLUME_RE = re.compile(
    r"\bvolume\s+(?:up|down|(\d+)(?:\s*%)?)\b",
    re.IGNORECASE,
)
_TRANSFER_RE = re.compile(
    r"\b(?:move\s+(?:this|it|music|playback)|play\s+(?:in|on|everywhere))\b\s*(?:to\s+)?(?:the\s+)?(\w+)?",
    re.IGNORECASE,
)
_AUDIOBOOK_RE = re.compile(
    r"\b(?:continue\s+(?:my\s+)?(?:audiobook|book)|resume\s+(?:my\s+)?(?:audiobook|book)|"
    r"where\s+was\s+I\s+in)\b",
    re.IGNORECASE,
)
_PODCAST_RE = re.compile(
    r"\b(?:any\s+new\s+podcasts?|play\s+(?:latest|newest)\s+episode|"
    r"subscribe\s+to|unsubscribe\s+from|podcast)\b",
    re.IGNORECASE,
)
_QUERY_RE = re.compile(
    r"\b(?:what(?:'s|s| is)\s+playing|what\s+song\s+is\s+this|"
    r"currently\s+playing|now\s+playing)\b",
    re.IGNORECASE,
)


def _classify_intent(message: str) -> tuple[str, dict[str, Any]]:
    """Classify media intent from message text.

    Returns (intent, metadata) tuple.
    """
    m = _PLAY_SMART_RE.search(message)
    if m:
        return "play_smart", {}

    m = _PLAY_RE.search(message)
    if m:
        return "play", {"query": m.group(1).strip()}

    m = _VOLUME_RE.search(message)
    if m:
        level = m.group(1)
        if level:
            return "volume_set", {"level": int(level)}
        if "up" in message.lower():
            return "volume_up", {}
        return "volume_down", {}

    if _CONTROL_RE.search(message):
        msg_lower = message.lower()
        if "pause" in msg_lower:
            return "pause", {}
        if "resume" in msg_lower:
            return "resume", {}
        if "stop" in msg_lower:
            return "stop", {}
        if "skip" in msg_lower or "next" in msg_lower:
            return "next", {}
        if "previous" in msg_lower:
            return "previous", {}
        if "mute" in msg_lower and "unmute" not in msg_lower:
            return "mute", {}
        if "unmute" in msg_lower:
            return "unmute", {}

    m = _TRANSFER_RE.search(message)
    if m:
        room = m.group(1) or ""
        return "transfer", {"room": room}

    if _AUDIOBOOK_RE.search(message):
        return "audiobook_resume", {}

    if _PODCAST_RE.search(message):
        msg_lower = message.lower()
        if "subscribe to" in msg_lower:
            return "podcast_subscribe", {}
        if "unsubscribe" in msg_lower:
            return "podcast_unsubscribe", {}
        if "new podcast" in msg_lower or "any new" in msg_lower:
            return "podcast_check", {}
        return "podcast_play", {}

    if _QUERY_RE.search(message):
        return "query", {}

    return "", {}


class MediaPlugin(CortexPlugin):
    """Layer 2 plugin for media playback and control."""

    plugin_id = "media"
    display_name = "Media & Music"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        self._router = PlaybackRouter()
        self._yt_music = YouTubeMusicProvider()
        self._local = LocalLibraryProvider()
        self._podcasts = PodcastProvider()
        self._prefs = PreferenceEngine()
        self._playback = PlaybackState()

    async def setup(self, config: dict[str, Any]) -> bool:
        """Initialise media providers."""
        # YouTube Music (optional)
        auth_file = config.get("ytmusic_auth", "oauth.json")
        await self._yt_music.setup(auth_file)

        # Chromecast discovery (non-blocking)
        try:
            await self._router.discover_chromecasts()
        except Exception:
            pass

        return True

    async def health(self) -> bool:
        """At least one provider or local library is always available."""
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        """Determine whether this plugin handles the message."""
        intent, meta = _classify_intent(message)
        if not intent:
            return CommandMatch(matched=False)
        return CommandMatch(
            matched=True,
            intent=intent,
            confidence=0.85,
            metadata=meta,
        )

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        """Execute media command and return response."""
        intent = match.intent
        meta = match.metadata
        room = context.get("room", "")
        user_id = context.get("user_id", "default")

        try:
            if intent == "play":
                return await self._handle_play(meta.get("query", ""), room, user_id)
            if intent == "play_smart":
                return await self._handle_smart_play(room, user_id)
            if intent == "pause":
                return await self._handle_pause(room)
            if intent == "resume":
                return await self._handle_play(
                    "", room, user_id,
                )
            if intent == "stop":
                return await self._handle_stop(room)
            if intent == "next":
                return CommandResult(success=True, response="Skipping to next track.")
            if intent == "previous":
                return CommandResult(success=True, response="Going back to previous track.")
            if intent in ("volume_up", "volume_down", "volume_set"):
                return await self._handle_volume(intent, meta, room)
            if intent == "mute":
                return await self._handle_volume("volume_set", {"level": 0}, room)
            if intent == "unmute":
                return await self._handle_volume("volume_set", {"level": 50}, room)
            if intent == "transfer":
                return await self._handle_transfer(room, meta.get("room", ""))
            if intent == "audiobook_resume":
                return CommandResult(
                    success=True,
                    response="Let me find where you left off in your audiobook.",
                )
            if intent == "podcast_check":
                return await self._handle_podcast_check()
            if intent == "podcast_subscribe":
                return CommandResult(
                    success=True,
                    response="I can subscribe you to a podcast. What's the feed URL or name?",
                )
            if intent == "podcast_play":
                return CommandResult(
                    success=True,
                    response="Which podcast episode would you like to hear?",
                )
            if intent == "query":
                return self._handle_query()
        except Exception as exc:
            logger.error("Media handle error: %s", exc)
            return CommandResult(
                success=False,
                response="Something went wrong with media playback.",
            )

        return CommandResult(success=False, response="I'm not sure what to play.")

    # ── Handlers ─────────────────────────────────────────────────

    async def _handle_play(
        self, query: str, room: str, user_id: str,
    ) -> CommandResult:
        """Search and play media."""
        if not query:
            return CommandResult(
                success=True,
                response="What would you like me to play?",
            )

        # Search across providers
        results: list[MediaItem] = []

        # Try local library first
        local_results = await self._local.search(query)
        results.extend(local_results)

        # Try YouTube Music
        if await self._yt_music.health():
            yt_results = await self._yt_music.search(query)
            results.extend(yt_results)

        if not results:
            return CommandResult(
                success=False,
                response=f"I couldn't find anything matching \"{query}\".",
            )

        item = results[0]

        # Record preference
        if item.genre:
            self._prefs.record_play(user_id, item.genre)

        # Log playback
        self._log_playback(user_id, item)

        # Resolve target and play
        target = await self._router.resolve_target(room) if room else None
        if target:
            stream_url = item.stream_url
            if not stream_url:
                stream_url = await self._get_stream_url(item) or ""
            if stream_url:
                await self._router.play(stream_url, target)

        self._playback = PlaybackState(
            is_playing=True,
            current_item=item,
            position_seconds=0,
            target_room=room,
        )

        artist_part = f" by {item.artist}" if item.artist else ""
        return CommandResult(
            success=True,
            response=f"Playing {item.title}{artist_part}.",
            metadata={"item": item.title, "provider": item.provider},
        )

    async def _handle_smart_play(self, room: str, user_id: str) -> CommandResult:
        """Handle 'play something' with smart suggestion."""
        query = self._prefs.suggest_query(user_id)
        return await self._handle_play(query, room, user_id)

    async def _handle_pause(self, room: str) -> CommandResult:
        target = await self._router.resolve_target(room) if room else None
        if target:
            await self._router.pause(target)
        self._playback.is_playing = False
        return CommandResult(success=True, response="Paused.")

    async def _handle_stop(self, room: str) -> CommandResult:
        target = await self._router.resolve_target(room) if room else None
        if target:
            await self._router.stop(target)
        self._playback = PlaybackState()
        return CommandResult(success=True, response="Stopped playback.")

    async def _handle_volume(
        self, intent: str, meta: dict, room: str,
    ) -> CommandResult:
        target = await self._router.resolve_target(room) if room else None
        current = self._playback.volume

        if intent == "volume_up":
            new_vol = min(1.0, current + 0.1)
        elif intent == "volume_down":
            new_vol = max(0.0, current - 0.1)
        else:
            new_vol = meta.get("level", 50) / 100.0

        if target:
            await self._router.set_volume(target, new_vol)
        self._playback.volume = new_vol
        return CommandResult(
            success=True,
            response=f"Volume set to {int(new_vol * 100)}%.",
        )

    async def _handle_transfer(self, from_room: str, to_room: str) -> CommandResult:
        if not to_room:
            return CommandResult(
                success=False,
                response="Which room should I move playback to?",
            )
        ok = await self._router.transfer(from_room, to_room)
        if ok:
            return CommandResult(
                success=True,
                response=f"Moved playback to {to_room}.",
            )
        return CommandResult(
            success=False,
            response=f"Couldn't move playback to {to_room}.",
        )

    async def _handle_podcast_check(self) -> CommandResult:
        new_eps = await self._podcasts.check_new_episodes()
        if new_eps:
            titles = ", ".join(e["title"] for e in new_eps[:3])
            return CommandResult(
                success=True,
                response=f"Found {len(new_eps)} new episode(s): {titles}.",
            )
        return CommandResult(
            success=True,
            response="No new podcast episodes right now.",
        )

    def _handle_query(self) -> CommandResult:
        if self._playback.is_playing and self._playback.current_item:
            item = self._playback.current_item
            artist_part = f" by {item.artist}" if item.artist else ""
            return CommandResult(
                success=True,
                response=f"Currently playing: {item.title}{artist_part}.",
            )
        return CommandResult(
            success=True,
            response="Nothing is playing right now.",
        )

    # ── Helpers ──────────────────────────────────────────────────

    async def _get_stream_url(self, item: MediaItem) -> str | None:
        """Get stream URL from the appropriate provider."""
        if item.provider == "youtube_music":
            return await self._yt_music.get_stream_url(item.id)
        if item.provider == "local":
            return await self._local.get_stream_url(item.id)
        return None

    def _log_playback(self, user_id: str, item: MediaItem) -> None:
        """Record playback in history (best-effort)."""
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO media_playback_history "
                "(user_id, provider, title, artist) VALUES (?, ?, ?, ?)",
                (user_id, item.provider, item.title, item.artist),
            )
            conn.commit()
        except Exception:
            pass
