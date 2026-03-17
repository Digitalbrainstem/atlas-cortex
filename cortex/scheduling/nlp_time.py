"""Natural-language time parser for Atlas scheduling.

Pure regex-based, <1 ms, zero external dependencies.  Handles durations,
absolute times, relative offsets, recurrence patterns and natural phrases
like "tomorrow morning" or "tonight at 8".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

# ── Result dataclass ──────────────────────────────────────────────

@dataclass
class ParsedTime:
    duration_seconds: int | None = None
    absolute_time: datetime | None = None
    cron_expression: str | None = None
    is_recurring: bool = False
    raw_text: str = ""


# ── Internal helpers ──────────────────────────────────────────────

_WORD_TO_NUM: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "a": 1, "an": 1,
}

_DOW_MAP: dict[str, int] = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 0,
    "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0,
}


def _word_to_int(word: str) -> int | None:
    """Convert a number word or digit string to int."""
    word = word.strip().lower()
    if word.isdigit():
        return int(word)
    return _WORD_TO_NUM.get(word)


def _parse_hm(hour: int, minute: int, ampm: str | None, now: datetime) -> datetime:
    """Build a datetime from hour/minute/ampm, rolling to tomorrow if past."""
    if ampm:
        ampm = ampm.lower().rstrip(".")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt <= now:
        dt += timedelta(days=1)
    return dt


# ── Duration parsing ─────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(?:(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty)\s+"
    r"(?:and\s+(?:a\s+)?)?)"
    r"?(hour|hr|minute|min|second|sec|day)s?"
    r"(?:\s+and\s+(?:a\s+)?half)?",
    re.IGNORECASE,
)

_HALF_UNIT_RE = re.compile(
    r"(?:a\s+)?half\s+(?:an?\s+)?(hour|hr|minute|min|second|sec|day)s?",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "hour": 3600, "hr": 3600,
    "minute": 60, "min": 60,
    "second": 1, "sec": 1,
    "day": 86400,
}

_AND_A_HALF_RE = re.compile(
    r"(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:and\s+a\s+half\s+)?(hour|hr|minute|min|second|sec|day)s?"
    r"\s+and\s+a\s+half",
    re.IGNORECASE,
)


def _parse_duration(text: str) -> int | None:
    """Return total seconds parsed from the text, or None."""
    total = 0
    found = False

    # "an hour and a half", "2 hours and a half"
    for m in _AND_A_HALF_RE.finditer(text):
        num = _word_to_int(m.group(1)) or 1
        unit = m.group(2).lower()
        secs = _UNIT_SECONDS.get(unit, 0)
        total += int(num * secs + secs / 2)
        found = True

    if not found:
        # "half an hour", "half a minute"
        for m in _HALF_UNIT_RE.finditer(text):
            unit = m.group(1).lower()
            total += _UNIT_SECONDS.get(unit, 0) // 2
            found = True

    if not found:
        # standard "5 minutes", "an hour", "2 hours 15 minutes"
        for m in _DURATION_RE.finditer(text):
            num_str = m.group(1) or "1"
            num = _word_to_int(num_str) or 1
            unit = m.group(2).lower()
            secs = _UNIT_SECONDS.get(unit, 0)
            total += num * secs
            # "… and a half" suffix already captured in main regex group
            if "and a half" in m.group(0).lower():
                total += secs // 2
            found = True

    return total if found else None


# ── Absolute time parsing ────────────────────────────────────────

_TIME_12_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
    re.IGNORECASE,
)

_TIME_24_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")

_NAMED_TIME: dict[str, tuple[int, int]] = {
    "noon": (12, 0), "midday": (12, 0),
    "midnight": (0, 0),
}

_MORNING_EVENING_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s+(?:in\s+the\s+)?(morning|evening|at\s+night)\b",
    re.IGNORECASE,
)


def _parse_absolute(text: str, now: datetime) -> datetime | None:
    """Parse an absolute time reference and return a datetime."""
    low = text.lower()

    for name, (h, m) in _NAMED_TIME.items():
        if re.search(r"\b" + name + r"\b", low):
            return _parse_hm(h, m, None, now)

    m = _MORNING_EVENING_RE.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        period = m.group(3).lower().strip()
        if "morning" in period:
            ampm = "am"
        else:
            ampm = "pm"
        return _parse_hm(hour, minute, ampm, now)

    m = _TIME_12_RE.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        return _parse_hm(hour, minute, m.group(3), now)

    m = _TIME_24_RE.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return _parse_hm(hour, minute, None, now)

    return None


# ── Relative time parsing ────────────────────────────────────────

_RELATIVE_RE = re.compile(
    r"\bin\s+(.+?)(?:\s+from\s+now)?\s*$",
    re.IGNORECASE,
)


def _parse_relative(text: str, now: datetime) -> datetime | None:
    """Parse 'in 15 minutes', 'in an hour', etc."""
    m = _RELATIVE_RE.search(text)
    if not m:
        return None
    dur = _parse_duration(m.group(1))
    if dur is None:
        return None
    return now + timedelta(seconds=dur)


# ── Recurrence parsing ───────────────────────────────────────────

_EVERY_DURATION_RE = re.compile(
    r"\bevery\s+(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(hour|hr|minute|min|second|sec|day)s?\b",
    re.IGNORECASE,
)

_EVERY_DOW_RE = re.compile(
    r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun)\b"
    r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?)?",
    re.IGNORECASE,
)

_DAILY_AT_RE = re.compile(
    r"\bdaily\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)

_EVERY_WEEKDAY_RE = re.compile(
    r"\bevery\s+weekday\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)


def _parse_recurrence(text: str) -> str | None:
    """Return a cron expression for recurring patterns, or None."""
    low = text.lower()

    # "every weekday at 7am"
    m = _EVERY_WEEKDAY_RE.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm and ampm.lower().rstrip(".") == "pm" and hour < 12:
            hour += 12
        elif ampm and ampm.lower().rstrip(".") == "am" and hour == 12:
            hour = 0
        return f"{minute} {hour} * * 1-5"

    # "daily at noon"
    m = _DAILY_AT_RE.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm and ampm.lower().rstrip(".") == "pm" and hour < 12:
            hour += 12
        elif ampm and ampm.lower().rstrip(".") == "am" and hour == 12:
            hour = 0
        return f"{minute} {hour} * * *"
    if "daily at noon" in low:
        return "0 12 * * *"
    if "daily at midnight" in low:
        return "0 0 * * *"

    # "every Monday at 9am"
    m = _EVERY_DOW_RE.search(text)
    if m:
        dow = _DOW_MAP.get(m.group(1).lower(), 1)
        hour = int(m.group(2) or 9)
        minute = int(m.group(3) or 0)
        ampm = m.group(4)
        if ampm and ampm.lower().rstrip(".") == "pm" and hour < 12:
            hour += 12
        elif ampm and ampm.lower().rstrip(".") == "am" and hour == 12:
            hour = 0
        return f"{minute} {hour} * * {dow}"

    # "every 2 hours"
    m = _EVERY_DURATION_RE.search(text)
    if m:
        num = _word_to_int(m.group(1)) or 1
        unit = m.group(2).lower()
        if unit in ("hour", "hr"):
            return f"0 */{num} * * *"
        if unit in ("minute", "min"):
            return f"*/{num} * * * *"
        if unit == "day":
            return f"0 0 */{num} * *"

    return None


# ── Natural phrasing ─────────────────────────────────────────────

def _parse_natural(text: str, now: datetime) -> datetime | None:
    """Handle 'tomorrow morning', 'tonight at 8', 'this evening', etc."""
    low = text.lower().strip()
    base = now

    tomorrow = False
    if "tomorrow" in low:
        base = now + timedelta(days=1)
        tomorrow = True

    # extract optional time from phrase
    time_m = re.search(
        r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
        low,
    )
    if time_m:
        hour = int(time_m.group(1))
        minute = int(time_m.group(2) or 0)
        ampm = time_m.group(3)
        # Infer PM when context implies evening/night and no explicit am/pm
        if not ampm and hour <= 12:
            if "tonight" in low or "evening" in low or "night" in low:
                if hour < 12:
                    hour += 12
        if ampm and ampm.lower().rstrip(".") == "pm" and hour < 12:
            hour += 12
        elif ampm and ampm.lower().rstrip(".") == "am" and hour == 12:
            hour = 0
        dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not tomorrow and dt <= now:
            dt += timedelta(days=1)
        return dt

    if "morning" in low:
        return base.replace(hour=8, minute=0, second=0, microsecond=0)
    if "evening" in low or "tonight" in low:
        target = base if not tomorrow else base
        return target.replace(hour=20, minute=0, second=0, microsecond=0)
    if "afternoon" in low:
        return base.replace(hour=14, minute=0, second=0, microsecond=0)
    if "night" in low:
        return base.replace(hour=21, minute=0, second=0, microsecond=0)

    if tomorrow:
        return base.replace(hour=9, minute=0, second=0, microsecond=0)

    return None


# ── Public API ────────────────────────────────────────────────────

def parse_time(text: str, *, now: datetime | None = None) -> ParsedTime:
    """Parse a natural-language time expression.

    Returns a ``ParsedTime`` with whichever fields could be extracted.
    """
    if now is None:
        now = datetime.now()

    result = ParsedTime(raw_text=text)

    # 1. Try recurrence first (most specific)
    cron = _parse_recurrence(text)
    if cron:
        result.cron_expression = cron
        result.is_recurring = True
        return result

    # 2. Relative ("in 15 minutes")
    abs_rel = _parse_relative(text, now)
    if abs_rel:
        result.absolute_time = abs_rel
        dur = _parse_duration(text)
        if dur:
            result.duration_seconds = dur
        return result

    # 3. Absolute ("7am", "3:30 PM")
    abs_t = _parse_absolute(text, now)
    if abs_t:
        result.absolute_time = abs_t
        return result

    # 4. Natural phrasing ("tomorrow morning")
    nat = _parse_natural(text, now)
    if nat:
        result.absolute_time = nat
        return result

    # 5. Plain duration ("5 minutes")
    dur = _parse_duration(text)
    if dur:
        result.duration_seconds = dur
        return result

    return result
