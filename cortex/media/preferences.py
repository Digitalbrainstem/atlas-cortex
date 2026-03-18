"""Preference engine — genre affinity tracking and smart suggestions.

# Module ownership: User media preference tracking and time-based suggestions
"""

from __future__ import annotations

import logging
from datetime import datetime

from cortex.db import get_db

logger = logging.getLogger(__name__)

# Time-of-day genre defaults
_TIME_GENRES: dict[str, list[str]] = {
    "morning": ["jazz", "classical", "acoustic", "indie"],
    "afternoon": ["pop", "rock", "electronic", "hip-hop"],
    "evening": ["r&b", "soul", "jazz", "ambient"],
    "late_night": ["ambient", "lo-fi", "chillwave", "classical"],
}


class PreferenceEngine:
    """Tracks genre affinities and generates smart suggestions."""

    def record_play(self, user_id: str, genre: str) -> None:
        """Record a play event and update genre affinity."""
        if not genre:
            return
        genre = genre.lower().strip()
        conn = get_db()
        conn.execute(
            "INSERT INTO media_preferences (user_id, genre, affinity, play_count, last_played) "
            "VALUES (?, ?, 0.6, 1, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, genre) DO UPDATE SET "
            "play_count = play_count + 1, "
            "affinity = MIN(1.0, affinity + 0.05), "
            "last_played = CURRENT_TIMESTAMP",
            (user_id, genre),
        )
        conn.commit()

    def get_preferred_genres(self, user_id: str, limit: int = 5) -> list[str]:
        """Return top genres by affinity for a user."""
        conn = get_db()
        rows = conn.execute(
            "SELECT genre FROM media_preferences "
            "WHERE user_id = ? ORDER BY affinity DESC, play_count DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def get_time_preference(self, user_id: str) -> str:
        """Based on play history, suggest a genre for the current time of day."""
        hour = datetime.now().hour
        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 17:
            period = "afternoon"
        elif 17 <= hour < 22:
            period = "evening"
        else:
            period = "late_night"

        # Check if user has preferred genres matching this time period
        preferred = self.get_preferred_genres(user_id, limit=10)
        time_genres = _TIME_GENRES.get(period, [])

        for genre in preferred:
            if genre in time_genres:
                return genre

        # Fall back to time-based default
        return time_genres[0] if time_genres else "pop"

    def suggest_query(self, user_id: str) -> str:
        """Generate a smart suggestion for 'play something'."""
        genre = self.get_time_preference(user_id)
        hour = datetime.now().hour

        if 5 <= hour < 9:
            return f"upbeat {genre} morning music"
        if 9 <= hour < 12:
            return f"focus {genre} music"
        if 12 <= hour < 17:
            return f"energetic {genre}"
        if 17 <= hour < 21:
            return f"relaxing {genre} evening vibes"
        return f"chill {genre} late night"
