"""Analysis outcome helpers: success criteria and stale RUNNING detection.

Cloud Run Job execution COMPLETE must not be treated as analysis success.
Formal success requires analysis_status OK/DEGRADED, a non-null
last_successful_analysis_at, and a published current.json.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_analysis_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def running_reference_timestamp(status: dict[str, Any] | None) -> str | None:
    """Prefer heartbeat_at, then started_at, then generated_at for RUNNING age."""
    if not status:
        return None
    raw = status.get("heartbeat_at") or status.get("started_at") or status.get("generated_at")
    return str(raw) if raw else None


def is_stale_running(
    status: dict[str, Any] | None,
    *,
    stale_minutes: float,
    now: datetime | None = None,
) -> bool:
    """True when status is RUNNING and the running reference is older than stale_minutes.

    With heartbeat_at, a healthy Analyzer that runs longer than analysis_stale_minutes
    (e.g. ~19–30+ min on 72h data) is not considered stale while heartbeats continue.
    Legacy RUNNING payloads without heartbeat_at fall back to started_at/generated_at.
    """
    if not status or status.get("status") != "RUNNING":
        return False
    stamp = parse_analysis_timestamp(running_reference_timestamp(status))
    if stamp is None:
        return True
    current = now or datetime.now(UTC)
    age_seconds = (current - stamp).total_seconds()
    return age_seconds > float(stale_minutes) * 60.0


def is_analysis_success(
    status: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> bool:
    """Formal analysis success (independent of Cloud Run Job COMPLETE)."""
    if not status or not current:
        return False
    if status.get("status") not in {"OK", "DEGRADED"}:
        return False
    if not status.get("last_successful_analysis_at"):
        return False
    return True
