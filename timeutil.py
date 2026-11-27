"""Timestamp helpers (no Discord imports, so they are easy to test)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

CLOCK_12 = {"12", "12h", "12hr", "12-hour", "12 hour", "normal", "regular", "am/pm", "ampm"}
CLOCK_24 = {"24", "24h", "24hr", "24-hour", "24 hour", "military", "24-hour clock (military)"}


def valid_timezone(name: Optional[str]) -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo((name or "").strip())
        return bool((name or "").strip())
    except Exception:
        return False


def _now(tz: Optional[str] = None) -> datetime:
    """Current time in the given IANA zone, else CARD_TIMEZONE, else UTC."""
    from zoneinfo import ZoneInfo

    for name in (tz, os.environ.get("CARD_TIMEZONE")):
        if name and valid_timezone(name):
            return datetime.now(ZoneInfo(name.strip()))
    return datetime.now(timezone.utc)


def _format(moment: datetime, military: bool) -> str:
    if military:
        return f"Today at {moment.strftime('%H:%M')}"
    return f"Today at {moment.strftime('%I:%M %p').lstrip('0')}"


def default_timestamp(military: bool = False, tz: Optional[str] = None) -> str:
    """'Today at 8:44 PM' (or 'Today at 20:44' in 24-hour time)."""
    return _format(_now(tz), military)


def resolve_timestamp(value: Optional[str], tz: Optional[str] = None) -> str:
    """Empty or a 12h keyword -> 12-hour clock; 24h / military -> 24-hour
    clock; anything else is shown exactly as typed."""
    text = (value or "").strip()
    key = text.lower()
    if not text or key in CLOCK_12:
        return default_timestamp(False, tz)
    if key in CLOCK_24:
        return default_timestamp(True, tz)
    return text[:60]


def conversation_timestamps(count: int, value: Optional[str], tz: Optional[str] = None) -> list[str]:
    """One timestamp per message group, a minute apart and ending now. Custom
    text is repeated for every group."""
    text = (value or "").strip()
    key = text.lower()
    if text and key not in CLOCK_12 | CLOCK_24:
        return [text[:60]] * count
    military = key in CLOCK_24
    now = _now(tz)
    return [_format(now - timedelta(minutes=count - 1 - i), military) for i in range(count)]


_DURATION_RE = re.compile(r"(\d+)\s*([a-z]+)")
_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
    "mo": 30 * 86400, "mos": 30 * 86400, "month": 30 * 86400, "months": 30 * 86400,
    "y": 365 * 86400, "yr": 365 * 86400, "yrs": 365 * 86400, "year": 365 * 86400, "years": 365 * 86400,
}
_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(?:\d+|@me)/(\d+)/(\d+)")


def parse_duration(text: str) -> Optional[int]:
    """'1h30m', '2d', '45 min', '3 months', '1y' style text -> seconds, or
    None if it isn't a duration. Months are 30 days, years 365."""
    cleaned = (text or "").strip().lower().replace(",", " ")
    if not cleaned:
        return None
    total, matched = 0, 0
    for number, unit in _DURATION_RE.findall(cleaned):
        if unit not in _UNIT_SECONDS:
            return None
        total += int(number) * _UNIT_SECONDS[unit]
        matched += 1
    leftover = _DURATION_RE.sub("", cleaned).strip()
    if not matched or leftover:
        return None
    return total or None


def parse_message_ref(text: str) -> Optional[int]:
    """A message ID or a message link -> the message ID."""
    text = (text or "").strip()
    match = _LINK_RE.search(text)
    if match:
        return int(match.group(2))
    return int(text) if text.isdigit() else None
