"""OFFLINE falsely baked when last_update lags generated_at."""

from __future__ import annotations


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
    stamps = [overview.get("last_update"), overview.get("generated_at")]
    stamps = [s for s in stamps if s]
    if not stamps:
        return "ERROR"
    from datetime import datetime

    ages = []
    for s in stamps:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000
        ages.append((now_ms - ts) / 60_000)
    age_min = min(ages)
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


def test_fresh_generated_at_overrides_stale_last_update() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "OFFLINE",
        "last_update": (now - timedelta(minutes=55)).isoformat(),
        "generated_at": (now - timedelta(minutes=2)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert (
        effective_status(overview, now_ms=now.timestamp() * 1000) == "COLLECTING"
    )


def test_both_stamps_old_still_offline() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    overview = {
        "status": "COLLECTING",
        "last_update": (now - timedelta(minutes=55)).isoformat(),
        "generated_at": (now - timedelta(minutes=50)).isoformat(),
        "markets": 205,
        "samples_written": 10000,
    }
    assert effective_status(overview, now_ms=now.timestamp() * 1000) == "OFFLINE"
