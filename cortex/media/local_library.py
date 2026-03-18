"""Local music library provider — scan, index, and search local audio files.

# Module ownership: Local file library with tag reading and FTS5 search
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cortex.db import get_db
from cortex.media.base import MediaItem, MediaProvider

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".wav", ".m4a", ".opus", ".wma", ".aac"}


class LocalLibraryProvider(MediaProvider):
    """Provider for locally-stored audio files."""

    provider_id = "local"
    display_name = "Local Library"
    supports_streaming = True

    async def scan_directory(self, path: str) -> int:
        """Scan directory for audio files, read tags, and index in DB.

        Returns the count of files indexed.
        """
        root = Path(path)
        if not root.is_dir():
            logger.warning("Scan path does not exist: %s", path)
            return 0

        conn = get_db()
        count = 0

        for dirpath, _dirs, filenames in os.walk(root):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _AUDIO_EXTENSIONS:
                    continue
                fpath = os.path.join(dirpath, fname)
                tags = self._read_tags(fpath)
                title = tags.get("title", os.path.splitext(fname)[0])
                artist = tags.get("artist", "")
                album = tags.get("album", "")
                genre = tags.get("genre", "")
                duration = tags.get("duration", 0)

                conn.execute(
                    "INSERT OR REPLACE INTO media_library "
                    "(provider, media_type, title, artist, album, genre, "
                    " duration_seconds, file_path, metadata) "
                    "VALUES (?, 'song', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.provider_id,
                        title,
                        artist,
                        album,
                        genre,
                        duration,
                        fpath,
                        json.dumps(tags),
                    ),
                )
                count += 1

        conn.commit()
        logger.info("Indexed %d audio files from %s", count, path)
        return count

    def _read_tags(self, file_path: str) -> dict:
        """Read ID3/Vorbis/FLAC tags using mutagen (optional dep)."""
        try:
            import mutagen  # type: ignore[import-untyped]
        except ImportError:
            return {"file": file_path}

        try:
            audio = mutagen.File(file_path, easy=True)
            if audio is None:
                return {"file": file_path}

            tags: dict = {"file": file_path}
            for key in ("title", "artist", "album", "genre"):
                val = audio.get(key)
                if val:
                    tags[key] = val[0] if isinstance(val, list) else str(val)
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                tags["duration"] = float(audio.info.length)
            return tags
        except Exception as exc:
            logger.debug("Failed to read tags for %s: %s", file_path, exc)
            return {"file": file_path}

    # ── Search ───────────────────────────────────────────────────

    async def search(self, query: str, media_type: str = "") -> list[MediaItem]:
        """Search local library by title/artist/album."""
        conn = get_db()
        like = f"%{query}%"
        sql = (
            "SELECT id, title, artist, album, genre, duration_seconds, file_path, metadata "
            "FROM media_library WHERE provider = 'local' "
            "AND (title LIKE ? OR artist LIKE ? OR album LIKE ?) "
            "ORDER BY title LIMIT 20"
        )
        rows = conn.execute(sql, (like, like, like)).fetchall()

        items: list[MediaItem] = []
        for row in rows:
            items.append(MediaItem(
                id=str(row[0]),
                title=row[1] or "",
                artist=row[2] or "",
                album=row[3] or "",
                genre=row[4] or "",
                duration_seconds=row[5] or 0,
                provider=self.provider_id,
                media_type="song",
                stream_url=row[6] or "",
            ))
        return items

    # ── Stream URL ───────────────────────────────────────────────

    async def get_stream_url(self, item_id: str) -> str | None:
        """Return local file path as the stream URL."""
        conn = get_db()
        row = conn.execute(
            "SELECT file_path FROM media_library WHERE id = ? AND provider = 'local'",
            (item_id,),
        ).fetchone()
        if row:
            return row[0]
        return None

    # ── Health ───────────────────────────────────────────────────

    async def health(self) -> bool:
        """Local library is always available."""
        return True
