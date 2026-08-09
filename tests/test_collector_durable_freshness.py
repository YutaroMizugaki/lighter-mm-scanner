"""Collector health — durable event freshness vs sync-only success."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lighter_mm.cloud.health import collector_status_label
from lighter_mm.storage.state import RunState, now_iso


def _ms(minutes_ago: float) -> int:
    return int((datetime.now(UTC) - timedelta(minutes=minutes_ago)).timestamp() * 1000)


def _state(
    *,
    flush_minutes_ago: float = 2.0,
    event_minutes_ago: float | None = 2.0,
    sync_failures: int = 0,
    sync_error: str | None = None,
) -> RunState:
    flush_at = (datetime.now(UTC) - timedelta(minutes=flush_minutes_ago)).isoformat()
    return RunState(
        run_id="r1",
        started_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        status="running",
        last_successful_flush=flush_at,
        last_durable_event_ms=None if event_minutes_ago is None else _ms(event_minutes_ago),
        samples_written=1000,
    )


def test_sync_fresh_zero_upload_stale_event_not_collecting() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=60.0)
    assert (
        collector_status_label(state, ok_minutes=20, warn_minutes=40)
        == "OFFLINE"
    )


def test_sync_fresh_and_durable_event_fresh_is_collecting() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=5.0)
    assert (
        collector_status_label(state, ok_minutes=20, warn_minutes=40)
        == "COLLECTING"
    )


def test_sync_failure_is_degraded() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=5.0)
    assert (
        collector_status_label(
            state,
            ok_minutes=20,
            warn_minutes=40,
            consecutive_sync_failures=2,
            last_sync_error="upload failed",
        )
        == "DEGRADED"
    )


def test_durable_event_25_min_is_stale() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=25.0)
    assert (
        collector_status_label(state, ok_minutes=20, warn_minutes=40)
        == "STALE"
    )


def test_durable_event_45_min_is_offline() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=45.0)
    assert (
        collector_status_label(state, ok_minutes=20, warn_minutes=40)
        == "OFFLINE"
    )


def test_ws_degraded_with_fresh_event_is_degraded() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=5.0)
    assert (
        collector_status_label(
            state,
            ok_minutes=20,
            warn_minutes=40,
            ws_degraded=True,
        )
        == "DEGRADED"
    )


def test_stale_event_overrides_sync_degraded() -> None:
    state = _state(flush_minutes_ago=2.0, event_minutes_ago=45.0)
    assert (
        collector_status_label(
            state,
            ok_minutes=20,
            warn_minutes=40,
            consecutive_sync_failures=3,
        )
        == "OFFLINE"
    )


def test_cold_start_grace_without_event_can_collect() -> None:
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        samples_written=0,
    )
    assert (
        collector_status_label(state, ok_minutes=20, warn_minutes=40)
        == "COLLECTING"
    )
