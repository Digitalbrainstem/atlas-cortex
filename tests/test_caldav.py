"""Tests for CalDAV connector — mock CalDAV responses with sample iCalendar data."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.knowledge.caldav import CalDAVConnector, CalDAVError

SAMPLE_CALENDARS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:"
  xmlns:cs="urn:ietf:params:xml:ns:caldav"
  xmlns:ic="http://apple.com/ns/ical/">
  <d:response>
    <d:href>/caldav/user/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>User</d:displayname>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/caldav/user/personal/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Personal</d:displayname>
        <d:resourcetype>
          <d:collection/>
          <cs:calendar/>
        </d:resourcetype>
        <ic:calendar-color>#0000FFFF</ic:calendar-color>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/caldav/user/work/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Work</d:displayname>
        <d:resourcetype>
          <d:collection/>
          <cs:calendar/>
        </d:resourcetype>
        <ic:calendar-color>#FF0000FF</ic:calendar-color>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

SAMPLE_EVENTS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/caldav/user/personal/event1.ics</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"abc123"</d:getetag>
        <c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-001@example.com
SUMMARY:Team Meeting
DESCRIPTION:Weekly standup with the team
DTSTART:20250115T100000Z
DTEND:20250115T110000Z
LOCATION:Conference Room A
END:VEVENT
END:VCALENDAR</c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/caldav/user/personal/event2.ics</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"def456"</d:getetag>
        <c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-002@example.com
SUMMARY:Birthday Party
DTSTART;VALUE=DATE:20250120
DTEND;VALUE=DATE:20250121
LOCATION:Home
END:VEVENT
END:VCALENDAR</c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


def _mock_response(status_code=207, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class TestCalDAVListCalendars:
    async def test_list_calendars_parses_response(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com/caldav/user/",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_CALENDARS_XML)
        )
        connector._client = mock_client

        calendars = await connector.list_calendars()
        assert len(calendars) == 2
        names = {c["name"] for c in calendars}
        assert "Personal" in names
        assert "Work" in names

    async def test_list_calendars_includes_color(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com/caldav/user/",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_CALENDARS_XML)
        )
        connector._client = mock_client

        calendars = await connector.list_calendars()
        personal = next(c for c in calendars if c["name"] == "Personal")
        assert personal["color"] == "#0000FFFF"

    async def test_list_calendars_error(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(401, "Unauthorized")
        )
        connector._client = mock_client

        with pytest.raises(CalDAVError, match="PROPFIND failed"):
            await connector.list_calendars()


class TestCalDAVGetEvents:
    async def test_get_events_parses_icalendar(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_EVENTS_XML)
        )
        connector._client = mock_client

        events = await connector.get_events("https://caldav.example.com/personal/")
        assert len(events) == 2

        meeting = next(e for e in events if e["uid"] == "event-001@example.com")
        assert meeting["summary"] == "Team Meeting"
        assert meeting["location"] == "Conference Room A"
        assert meeting["all_day"] is False

    async def test_get_events_all_day(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_EVENTS_XML)
        )
        connector._client = mock_client

        events = await connector.get_events("https://caldav.example.com/personal/")
        birthday = next(e for e in events if e["uid"] == "event-002@example.com")
        assert birthday["summary"] == "Birthday Party"
        assert birthday["all_day"] is True
        assert birthday["start"] == "2025-01-20"

    async def test_get_events_with_time_range(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_EVENTS_XML)
        )
        connector._client = mock_client

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 2, 1, tzinfo=timezone.utc)
        events = await connector.get_events(
            "https://caldav.example.com/personal/", start=start, end=end
        )
        assert len(events) == 2
        # Verify the request was made with time-range in the body
        call_args = mock_client.request.call_args
        body = call_args.kwargs.get("content", call_args[1].get("content", b""))
        assert b"time-range" in body


class TestCalDAVGetUpcoming:
    async def test_get_upcoming_aggregates_calendars(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com/caldav/user/",
            username="user",
            password="pass",
        )
        connector.list_calendars = AsyncMock(return_value=[
            {"name": "Personal", "url": "https://caldav.example.com/personal/", "color": "#0000FF"},
        ])
        connector.get_events = AsyncMock(return_value=[
            {
                "uid": "ev1",
                "summary": "Dentist",
                "start": "2025-01-15T10:00:00+00:00",
                "end": "2025-01-15T11:00:00+00:00",
                "description": "",
                "location": "",
                "all_day": False,
            }
        ])

        events = await connector.get_upcoming(days=7)
        assert len(events) == 1
        assert events[0]["calendar"] == "Personal"


class TestCalDAVSyncToKnowledge:
    async def test_sync_indexes_events(self, db_conn):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        connector.list_calendars = AsyncMock(return_value=[
            {"name": "Personal", "url": "https://caldav.example.com/personal/", "color": None},
        ])
        connector.get_events = AsyncMock(return_value=[
            {
                "uid": "ev1@example.com",
                "summary": "Team Standup",
                "start": "2025-01-15T10:00:00+00:00",
                "end": "2025-01-15T10:30:00+00:00",
                "description": "Daily standup",
                "location": "Room 1",
                "all_day": False,
            },
        ])

        result = await connector.sync_to_knowledge(db_conn, owner_id="user1")
        assert result["calendars_synced"] == 1
        assert result["events_synced"] == 1

        row = db_conn.execute(
            "SELECT * FROM knowledge_docs WHERE source = 'caldav'"
        ).fetchone()
        assert row is not None

    async def test_sync_deduplicates_unchanged(self, db_conn):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        connector.list_calendars = AsyncMock(return_value=[
            {"name": "Work", "url": "https://caldav.example.com/work/", "color": None},
        ])
        events = [{
            "uid": "repeat@example.com",
            "summary": "Recurring",
            "start": "2025-01-15T09:00:00+00:00",
            "end": "2025-01-15T09:30:00+00:00",
            "description": "",
            "location": "",
            "all_day": False,
        }]
        connector.get_events = AsyncMock(return_value=events)

        # First sync
        r1 = await connector.sync_to_knowledge(db_conn, owner_id="user1")
        assert r1["events_synced"] == 1

        # Second sync — same data, should skip
        r2 = await connector.sync_to_knowledge(db_conn, owner_id="user1")
        assert r2["events_synced"] == 0


class TestCalDAVHealth:
    async def test_health_ok(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        connector.list_calendars = AsyncMock(return_value=[])
        assert await connector.health() is True

    async def test_health_failure(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        connector.list_calendars = AsyncMock(side_effect=CalDAVError("down"))
        assert await connector.health() is False


class TestICalendarParsing:
    def test_parse_icalendar_basic(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        ical = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "BEGIN:VEVENT\n"
            "UID:test-uid-123\n"
            "SUMMARY:Test Event\n"
            "DTSTART:20250115T100000Z\n"
            "DTEND:20250115T110000Z\n"
            "LOCATION:Room B\n"
            "DESCRIPTION:A test event\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = connector._parse_icalendar(ical)
        assert len(events) == 1
        assert events[0]["uid"] == "test-uid-123"
        assert events[0]["summary"] == "Test Event"
        assert events[0]["location"] == "Room B"
        assert "2025-01-15" in events[0]["start"]

    def test_parse_icalendar_all_day(self):
        connector = CalDAVConnector(
            url="https://caldav.example.com",
            username="user",
            password="pass",
        )
        ical = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:allday-001\n"
            "SUMMARY:Holiday\n"
            "DTSTART;VALUE=DATE:20250101\n"
            "DTEND;VALUE=DATE:20250102\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = connector._parse_icalendar(ical)
        assert len(events) == 1
        assert events[0]["all_day"] is True
        assert events[0]["start"] == "2025-01-01"
