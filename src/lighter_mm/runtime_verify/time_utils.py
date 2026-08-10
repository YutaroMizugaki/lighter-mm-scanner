"""Timestamp parsing and age helpers for runtime verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

JST = timezone(timedelta(hours=9))


def parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ms = int(value)
        if ms < 10_000_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def age_seconds(dt: datetime | None, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    now = now or datetime.now(UTC)
    return (now - dt).total_seconds()


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def format_ts_dual(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    jst = dt.astimezone(JST)
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S UTC')} / {jst.strftime('%H:%M:%S JST')}"
