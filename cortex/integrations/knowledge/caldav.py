"""CalDAV connector — sync calendar events for knowledge indexing (Phase I5.3)."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from cortex.integrations.knowledge.index import KnowledgeIndex
from cortex.integrations.knowledge.processor import DocumentProcessor

logger = logging.getLogger(__name__)

DAV_NS = "DAV:"
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_NS = "http://apple.com/ns/ical/"


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


class CalDAVError(Exception):
    """Base error for CalDAV operations."""


class CalDAVConnector:
    """Sync calendar events from a CalDAV server."""

    def __init__(self, url: str, username: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                auth=(self.username, self.password),
                timeout=30.0,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def list_calendars(self) -> list[dict]:
        """List available calendars.

        Returns ``[{name, url, color}]``.
        """
        client = await self._get_client()

        propfind_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<d:propfind xmlns:d="DAV:" '
            'xmlns:cs="urn:ietf:params:xml:ns:caldav" '
            'xmlns:ic="http://apple.com/ns/ical/">'
            "<d:prop>"
            "<d:displayname/>"
            "<d:resourcetype/>"
            "<ic:calendar-color/>"
            "</d:prop>"
            "</d:propfind>"
        )

        try:
            response = await client.request(
                "PROPFIND",
                self.url,
                content=propfind_body.encode("utf-8"),
                headers={"Content-Type": "application/xml", "Depth": "1"},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CalDAVError(f"Cannot reach CalDAV at {self.url}: {exc}") from exc

        if response.status_code not in (200, 207):
            raise CalDAVError(
                f"PROPFIND failed with status {response.status_code}"
            )

        return self._parse_calendars(response.text)

    async def get_events(
        self,
        calendar_url: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Fetch events from a calendar within a date range.

        Returns ``[{uid, summary, description, start, end, location, all_day}]``.
        """
        client = await self._get_client()

        time_range = ""
        if start or end:
            s = (start or datetime(2000, 1, 1, tzinfo=timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
            e = (end or datetime(2099, 12, 31, tzinfo=timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
            time_range = (
                f'<c:time-range start="{s}" end="{e}"/>'
            )

        report_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop>"
            "<d:getetag/>"
            "<c:calendar-data/>"
            "</d:prop>"
            "<c:filter>"
            '<c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            f"{time_range}"
            "</c:comp-filter>"
            "</c:comp-filter>"
            "</c:filter>"
            "</c:calendar-query>"
        )

        try:
            response = await client.request(
                "REPORT",
                calendar_url,
                content=report_body.encode("utf-8"),
                headers={"Content-Type": "application/xml", "Depth": "1"},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise CalDAVError(f"Cannot reach calendar at {calendar_url}: {exc}") from exc

        if response.status_code not in (200, 207):
            raise CalDAVError(
                f"REPORT failed with status {response.status_code}"
            )

        return self._parse_events(response.text)

    async def get_upcoming(self, days: int = 7) -> list[dict]:
        """Get upcoming events across all calendars."""
        calendars = await self.list_calendars()
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)

        all_events: list[dict] = []
        for cal in calendars:
            try:
                events = await self.get_events(cal["url"], start=now, end=end)
                for ev in events:
                    ev["calendar"] = cal["name"]
                all_events.extend(events)
            except CalDAVError as exc:
                logger.warning("Failed to fetch events from %s: %s", cal["name"], exc)

        # Sort by start time
        all_events.sort(key=lambda e: e.get("start", ""))
        return all_events

    async def sync_to_knowledge(
        self, conn: sqlite3.Connection, owner_id: str = "system"
    ) -> dict:
        """Sync calendar events into the knowledge index for querying."""
        stats = {"calendars_synced": 0, "events_synced": 0, "errors": 0}

        try:
            calendars = await self.list_calendars()
        except CalDAVError as exc:
            logger.error("CalDAV sync failed to list calendars: %s", exc)
            stats["errors"] += 1
            return stats

        index = KnowledgeIndex(conn)
        processor = DocumentProcessor()

        for cal in calendars:
            try:
                events = await self.get_events(cal["url"])
            except CalDAVError as exc:
                logger.warning("Failed to fetch events from %s: %s", cal["name"], exc)
                stats["errors"] += 1
                continue

            stats["calendars_synced"] += 1

            for event in events:
                uid = event.get("uid", "unknown")
                doc_id = f"caldav_{uid}"

                # Build a text representation for indexing
                parts = [f"Calendar Event: {event.get('summary', 'Untitled')}"]
                if event.get("start"):
                    parts.append(f"Start: {event['start']}")
                if event.get("end"):
                    parts.append(f"End: {event['end']}")
                if event.get("location"):
                    parts.append(f"Location: {event['location']}")
                if event.get("description"):
                    parts.append(f"Description: {event['description']}")

                text = "\n".join(parts)
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

                # Check if already indexed with same hash
                existing = conn.execute(
                    "SELECT content_hash FROM knowledge_docs WHERE doc_id = ?",
                    (f"{doc_id}_0",),
                ).fetchone()
                if existing and existing["content_hash"] == content_hash:
                    continue

                # Remove old version
                if existing:
                    index.remove_document(f"{doc_id}_0")

                chunks = processor.process_text(
                    text, doc_id, title=event.get("summary", "Calendar Event")
                )
                metadata = {
                    "doc_id": doc_id,
                    "owner_id": owner_id,
                    "access_level": "household",
                    "source": "caldav",
                    "source_path": cal["url"],
                    "content_type": "text/calendar",
                    "content_hash": content_hash,
                }
                index.add_document(chunks, metadata)
                stats["events_synced"] += 1

        return stats

    async def health(self) -> bool:
        """Test connectivity to the CalDAV server."""
        try:
            await self.list_calendars()
            return True
        except (CalDAVError, Exception):
            return False

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _parse_calendars(self, xml_text: str) -> list[dict]:
        """Parse PROPFIND response for calendars."""
        results: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse CalDAV XML: %s", exc)
            return results

        for resp in root.findall(_tag(DAV_NS, "response")):
            href_el = resp.find(_tag(DAV_NS, "href"))
            if href_el is None or href_el.text is None:
                continue

            propstat = resp.find(_tag(DAV_NS, "propstat"))
            if propstat is None:
                continue

            prop = propstat.find(_tag(DAV_NS, "prop"))
            if prop is None:
                continue

            # Must be a calendar (has resourcetype with calendar child)
            resource_type = prop.find(_tag(DAV_NS, "resourcetype"))
            if resource_type is None:
                continue
            if resource_type.find(_tag(CALDAV_NS, "calendar")) is None:
                continue

            name_el = prop.find(_tag(DAV_NS, "displayname"))
            color_el = prop.find(_tag(APPLE_NS, "calendar-color"))

            name = name_el.text if name_el is not None and name_el.text else "Unnamed"
            color = color_el.text if color_el is not None and color_el.text else None

            url = href_el.text
            if not url.startswith("http"):
                url = f"{self.url}{url}"

            results.append({"name": name, "url": url, "color": color})

        return results

    def _parse_events(self, xml_text: str) -> list[dict]:
        """Parse REPORT multistatus response containing calendar-data."""
        events: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse CalDAV events XML: %s", exc)
            return events

        for resp in root.findall(_tag(DAV_NS, "response")):
            propstat = resp.find(_tag(DAV_NS, "propstat"))
            if propstat is None:
                continue

            prop = propstat.find(_tag(DAV_NS, "prop"))
            if prop is None:
                continue

            cal_data_el = prop.find(_tag(CALDAV_NS, "calendar-data"))
            if cal_data_el is None or cal_data_el.text is None:
                continue

            parsed = self._parse_icalendar(cal_data_el.text)
            events.extend(parsed)

        return events

    def _parse_icalendar(self, ical_text: str) -> list[dict]:
        """Parse iCalendar text to extract VEVENT components."""
        events: list[dict] = []
        in_vevent = False
        current: dict[str, str] = {}

        for line in ical_text.splitlines():
            line = line.strip()
            if line == "BEGIN:VEVENT":
                in_vevent = True
                current = {}
            elif line == "END:VEVENT":
                in_vevent = False
                events.append(self._vevent_to_dict(current))
            elif in_vevent and ":" in line:
                # Handle properties like DTSTART;VALUE=DATE:20240101
                key_part, _, value = line.partition(":")
                key = key_part.split(";")[0].upper()
                current[key] = value

        return events

    def _vevent_to_dict(self, props: dict[str, str]) -> dict:
        """Convert VEVENT properties to a normalized dict."""
        dtstart_raw = props.get("DTSTART", "")
        dtend_raw = props.get("DTEND", "")

        all_day = len(dtstart_raw) == 8  # YYYYMMDD = all-day event

        return {
            "uid": props.get("UID", ""),
            "summary": props.get("SUMMARY", ""),
            "description": props.get("DESCRIPTION", ""),
            "start": self._parse_ical_datetime(dtstart_raw),
            "end": self._parse_ical_datetime(dtend_raw),
            "location": props.get("LOCATION", ""),
            "all_day": all_day,
        }

    @staticmethod
    def _parse_ical_datetime(raw: str) -> str:
        """Parse an iCalendar datetime string to ISO format."""
        if not raw:
            return ""
        raw = raw.strip()
        try:
            if len(raw) == 8:
                # All-day date: YYYYMMDD
                dt = datetime.strptime(raw, "%Y%m%d")
                return dt.date().isoformat()
            elif raw.endswith("Z"):
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ")
                return dt.replace(tzinfo=timezone.utc).isoformat()
            else:
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
                return dt.isoformat()
        except ValueError:
            return raw
