"""Collector freshness status — prefer last_successful_flush over generated_at."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


# Mirrors dashboard/lib/data.ts effectiveStatus — keep in sync.
def effective_status(
    overview: dict,
    ok_minutes: float = 20,
    warn_minutes: float = 40,
    *,
    now_ms: float,
) -> str:
    baked = overview.get("status") or "ERROR"
    if baked in {"COMPLETED", "ERROR"}:
        return baked
    stamp = overview.get("last_successful_flush") or overview.get("last_update")
    if not stamp:
        return "ERROR"
    ts = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
    age_min = (now_ms - ts) / 60_000
    analyzed = overview.get("markets_analyzed")
    if analyzed is None:
        analyzed = overview.get("markets") or 0
    samples = overview.get("samples_written") or 0
    if age_min > warn_minutes:
        return "OFFLINE"
    if age_min > ok_minutes:
        return "STALE"
    if baked == "DEGRADED":
        return "DEGRADED"
    if analyzed == 0 and samples > 0:
        return "DEGRADED"
    if analyzed > 0 and baked in {"OFFLINE", "STALE"}:
        return "COLLECTING"
    return baked


def status_health_note(status: str) -> str | None:
    """Mirrors dashboard/lib/data.ts statusHealthNote."""
    if status == "OFFLINE":
        return "Collector data has not been successfully refreshed for >40m."
    if status == "STALE":
        return "Collector data is older than 20m."
    return None


def test_fresh_last_successful_flush_is_collecting() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=2)).isoformat(),
        "generated_at": (now - timedelta(minutes=2)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert (
        effective_status(overview, now_ms=now.timestamp() * 1000) == "COLLECTING"
    )


def test_flush_20_to_40_min_is_stale() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=25)).isoformat(),
        "generated_at": (now - timedelta(minutes=25)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_status(overview, now_ms=now.timestamp() * 1000) == "STALE"
    assert "collector data is older than 20m" in (status_health_note("STALE") or "").lower()


def test_flush_over_40_min_is_offline() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_successful_flush": (now - timedelta(minutes=55)).isoformat(),
        "generated_at": (now - timedelta(minutes=55)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"
    note = (status_health_note("OFFLINE") or "").lower()
    assert "collector data" in note
    assert "latest.json is stale" not in note


def test_fresh_generated_at_with_stale_flush_is_offline() -> None:
    """generated_at alone must not mask a stalled collector flush."""
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "OFFLINE",
        "last_successful_flush": (now - timedelta(minutes=55)).isoformat(),
        "last_update": (now - timedelta(minutes=55)).isoformat(),
        "generated_at": (now - timedelta(minutes=2)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"


def test_both_stamps_old_still_offline() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_update": (now - timedelta(minutes=55)).isoformat(),
        "generated_at": (now - timedelta(minutes=50)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"
