"""Collector freshness status — durable event age (mirrors dashboard/lib/data.ts)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def effective_collector_status(
    overview: dict,
    ok_minutes: float = 20,
    warn_minutes: float = 40,
    *,
    now_ms: float,
) -> str:
    baked = overview.get("status") or "ERROR"
    if baked in {"COMPLETED", "ERROR"}:
        return baked
    sync_degraded = (overview.get("consecutive_sync_failures") or 0) > 0 or bool(
        overview.get("last_sync_error")
    )
    event_stamp = overview.get("last_durable_event_at")
    if not event_stamp:
        if sync_degraded:
            return "DEGRADED"
        return "STALE"
    ts = datetime.fromisoformat(event_stamp.replace("Z", "+00:00")).timestamp() * 1000
    age_min = (now_ms - ts) / 60_000
    if age_min > warn_minutes:
        return "OFFLINE"
    if age_min > ok_minutes:
        return "STALE"
    if sync_degraded or baked == "DEGRADED":
        return "DEGRADED"
    return "COLLECTING"


def status_health_note(status: str) -> str | None:
    if status == "OFFLINE":
        return "Market data has not been durably collected for >40m."
    if status == "STALE":
        return "Latest durable market event is older than 20m."
    return None


def test_fresh_durable_event_is_collecting() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "last_durable_event_at": (now - timedelta(minutes=2)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_collector_status(overview, now_ms=now.timestamp() * 1000) == "COLLECTING"


def test_fresh_sync_stale_durable_event_not_collecting() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "last_durable_event_at": (now - timedelta(minutes=60)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_collector_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"


def test_durable_event_25_to_40_min_is_stale() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "last_durable_event_at": (now - timedelta(minutes=25)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_collector_status(overview, now_ms=now.timestamp() * 1000) == "STALE"
    assert "older than 20m" in (status_health_note("STALE") or "").lower()


def test_durable_event_over_40_min_is_offline() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "last_durable_event_at": (now - timedelta(minutes=55)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_collector_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"


def test_sync_failure_with_fresh_durable_is_degraded() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "last_durable_event_at": (now - timedelta(minutes=2)).isoformat(),
        "consecutive_sync_failures": 2,
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_collector_status(overview, now_ms=now.timestamp() * 1000) == "DEGRADED"
