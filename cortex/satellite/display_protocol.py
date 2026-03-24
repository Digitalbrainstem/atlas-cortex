"""Display content protocol — Atlas controls what satellites show.

# Module ownership: Satellite display content routing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DisplayCommand:
    """A command to show content on a satellite display."""

    mode: str  # avatar, recipe, video, dashboard, weather, photos, timer, list, calendar, learning, media_player
    content: dict[str, Any] = field(default_factory=dict)
    target_room: str = ""  # Empty = all tablets
    duration_seconds: int = 0  # 0 = until changed, >0 = auto-return to avatar


# Supported display modes with their content schemas
DISPLAY_MODES: dict[str, dict[str, Any]] = {
    "avatar": {
        "description": "Default avatar face display",
        "content_schema": {},
    },
    "recipe": {
        "description": "Recipe card with ingredients, steps, and integrated timers",
        "content_schema": {
            "title": "str",
            "servings": "int",
            "prep_time": "str",
            "cook_time": "str",
            "ingredients": "list[str]",
            "steps": "list[{text: str, timer_seconds: int?}]",
            "image_url": "str?",
            "source": "str?",
        },
    },
    "video": {
        "description": "Video player (YouTube Premium, local files)",
        "content_schema": {
            "provider": "str",  # youtube, local, plex
            "video_id": "str",  # YouTube video ID or file path
            "title": "str?",
            "start_seconds": "int?",
            "autoplay": "bool?",
        },
        "providers": {
            "youtube": {
                "description": "YouTube video via embedded player",
                "auth_required": True,
                "embed_template": "https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&rel=0&modestbranding=1",
            },
            "local": {
                "description": "Local video file",
                "auth_required": False,
            },
            "plex": {
                "description": "Plex media (future — placeholder)",
                "auth_required": True,
                "ready": False,
            },
            "netflix": {
                "description": "Netflix (future — placeholder)",
                "auth_required": True,
                "ready": False,
            },
        },
    },
    "dashboard": {
        "description": "Home Assistant dashboard in iframe",
        "content_schema": {
            "url": "str",  # Must match configured HA_URL
            "panel": "str?",
        },
    },
    "weather": {
        "description": "Weather display card",
        "content_schema": {
            "temperature": "float",
            "condition": "str",
            "forecast": "list[{day, high, low, condition}]",
            "location": "str",
        },
    },
    "photos": {
        "description": "Photo slideshow",
        "content_schema": {
            "photos": "list[str]",
            "interval_seconds": "int",
            "transition": "str?",
        },
    },
    "timer": {
        "description": "Large countdown timer display",
        "content_schema": {
            "label": "str",
            "total_seconds": "int",
            "remaining_seconds": "int",
        },
    },
    "list": {
        "description": "Scrollable checklist (grocery, todo, etc.)",
        "content_schema": {
            "title": "str",
            "items": "list[{text: str, checked: bool}]",
        },
    },
    "calendar": {
        "description": "Calendar view (day or week)",
        "content_schema": {
            "view": "str",  # day, week
            "events": "list[{title, start, end, color?}]",
        },
    },
    "learning": {
        "description": "Educational game/quiz display",
        "content_schema": {
            "game": "str",  # number_quest, science_safari, word_wizard
            "question": "str",
            "choices": "list[str]?",
            "score": "int?",
            "streak": "int?",
        },
    },
    "media_player": {
        "description": "Now playing display with album art and controls",
        "content_schema": {
            "title": "str",
            "artist": "str",
            "album_art_url": "str?",
            "progress_seconds": "int",
            "duration_seconds": "int",
            "is_playing": "bool",
        },
    },
}


def validate_display_command(cmd: DisplayCommand) -> tuple[bool, str]:
    """Validate a display command before sending to satellite."""
    if cmd.mode not in DISPLAY_MODES:
        return False, f"Unknown display mode: {cmd.mode}"

    # Security: validate dashboard URL matches HA_URL
    if cmd.mode == "dashboard":
        import os

        ha_url = os.environ.get("HA_URL", "")
        dashboard_url = cmd.content.get("url", "")
        if ha_url and dashboard_url and not dashboard_url.startswith(ha_url):
            return False, f"Dashboard URL must start with configured HA_URL ({ha_url})"

    # Security: validate video provider
    if cmd.mode == "video":
        provider = cmd.content.get("provider", "")
        if provider not in DISPLAY_MODES["video"]["providers"]:
            return False, f"Unknown video provider: {provider}"
        provider_info = DISPLAY_MODES["video"]["providers"][provider]
        if not provider_info.get("ready", True):
            return False, f"Video provider '{provider}' is not yet implemented"

    return True, ""


async def send_display_command(
    room: str,
    mode: str,
    content: dict[str, Any] | None = None,
    duration: int = 0,
) -> bool:
    """Send a display command to satellites in a room."""
    cmd = DisplayCommand(
        mode=mode,
        content=content or {},
        target_room=room,
        duration_seconds=duration,
    )

    ok, err = validate_display_command(cmd)
    if not ok:
        logger.warning("Display command rejected: %s", err)
        return False

    try:
        from cortex.avatar.broadcast import broadcast_to_room

        await broadcast_to_room(room, {
            "type": "display",
            "mode": cmd.mode,
            "content": cmd.content,
            "duration": cmd.duration_seconds,
        })
        return True
    except Exception as e:
        logger.error("Failed to send display command: %s", e)
        return False
