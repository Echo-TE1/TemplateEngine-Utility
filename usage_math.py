"""Pure helpers for usage tracking (no Discord imports, easy to test)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

MB = 1024 * 1024
GB = 1024 * MB


def parse_net_dev(text: str) -> Optional[tuple[int, int]]:
    """Total (received, sent) bytes over every interface except loopback,
    from the contents of /proc/net/dev."""
    rx = tx = 0
    found = False
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        if name.strip() == "lo":
            continue
        fields = data.split()
        if len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
        except ValueError:
            continue
        found = True
    return (rx, tx) if found else None


def read_net() -> Optional[tuple[int, int]]:
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as handle:
            return parse_net_dev(handle.read())
    except OSError:
        return None


def month_key(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m")


def fmt_bytes(value: float) -> str:
    if value >= GB:
        return f"{value / GB:.2f} GB"
    if value >= MB:
        return f"{value / MB:.0f} MB"
    return f"{value / 1024:.0f} KB"


def percent(used: float, limit: float) -> float:
    return 100.0 * used / limit if limit > 0 else 0.0


def bar(pct: float, width: int = 12) -> str:
    filled = max(0, min(width, round(width * pct / 100)))
    return "▓" * filled + "░" * (width - filled)


def counter_delta(previous: tuple[int, int], current: tuple[int, int]) -> tuple[int, int]:
    """Bytes since the last sample; a smaller counter means the container
    restarted, so the new value is itself the delta."""
    drx = current[0] - previous[0]
    dtx = current[1] - previous[1]
    return (drx if drx >= 0 else current[0], dtx if dtx >= 0 else current[1])
