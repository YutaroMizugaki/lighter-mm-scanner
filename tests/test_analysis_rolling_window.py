"""Rolling analysis window bounds scheduled Analyzer recomputation."""

from __future__ import annotations

from lighter_mm.cloud.analyzer_target import _analysis_window_ms
from lighter_mm.storage.state import RunState


def _ms(iso: str) -> int:
    from datetime import UTC, datetime

    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def test_rolling_window_clamps_72h_run_to_24h() -> None:
    durable = _ms("2026-08-10T12:00:00+00:00")
    started = _ms("2026-08-07T12:00:00+00:00")
    state = RunState(
        run_id="r1",
        started_at="2026-08-07T12:00:00+00:00",
        status="running",
        last_successful_flush="2026-08-10T12:00:00+00:00",
        last_durable_event_ms=durable,
    )
    start_ms, end_ms, _, durable_ms = _analysis_window_ms(
        state,
        execution_start_ms=_ms("2026-08-10T12:05:00+00:00"),
        window_hours=24.0,
    )
    assert durable_ms == durable
    assert end_ms == durable
    assert start_ms == durable - 24 * 3600 * 1000
    assert start_ms > started
    assert (end_ms - start_ms) == 24 * 3600 * 1000


def test_rolling_window_uses_run_start_when_shorter_than_window() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-08-10T00:00:00+00:00",
        status="running",
        last_successful_flush="2026-08-10T12:00:00+00:00",
        last_durable_event_ms=_ms("2026-08-10T12:00:00+00:00"),
    )
    start_ms, end_ms, _, _ = _analysis_window_ms(
        state,
        execution_start_ms=_ms("2026-08-10T12:05:00+00:00"),
        window_hours=24.0,
    )
    assert start_ms == _ms("2026-08-10T00:00:00+00:00")
    assert end_ms == _ms("2026-08-10T12:00:00+00:00")
    assert (end_ms - start_ms) == 12 * 3600 * 1000


def test_completed_run_also_uses_rolling_window() -> None:
    """Final ranking uses the same bounded window so 72h runs stay within timeout."""
    state = RunState(
        run_id="r1",
        started_at="2026-08-07T00:00:00+00:00",
        status="completed",
        ended_at="2026-08-10T00:00:00+00:00",
        last_successful_flush="2026-08-10T00:05:00+00:00",
        last_durable_event_ms=_ms("2026-08-10T00:00:00+00:00"),
    )
    start_ms, end_ms, _, _ = _analysis_window_ms(
        state,
        execution_start_ms=_ms("2026-08-10T12:00:00+00:00"),
        window_hours=24.0,
    )
    assert end_ms == _ms("2026-08-10T00:00:00+00:00")
    assert start_ms == end_ms - 24 * 3600 * 1000


def test_window_zero_keeps_full_history_legacy() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-08-07T12:00:00+00:00",
        status="running",
        last_successful_flush="2026-08-10T12:00:00+00:00",
        last_durable_event_ms=_ms("2026-08-10T12:00:00+00:00"),
    )
    start_ms, end_ms, _, _ = _analysis_window_ms(
        state,
        execution_start_ms=_ms("2026-08-10T12:05:00+00:00"),
        window_hours=0.0,
    )
    assert start_ms == _ms("2026-08-07T12:00:00+00:00")
    assert end_ms == _ms("2026-08-10T12:00:00+00:00")


def test_window_none_keeps_full_history_legacy() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-08-07T12:00:00+00:00",
        status="running",
        last_successful_flush="2026-08-10T12:00:00+00:00",
        last_durable_event_ms=_ms("2026-08-10T12:00:00+00:00"),
    )
    start_ms, end_ms, _, _ = _analysis_window_ms(
        state,
        execution_start_ms=_ms("2026-08-10T12:05:00+00:00"),
        window_hours=None,
    )
    assert start_ms == _ms("2026-08-07T12:00:00+00:00")
    assert (end_ms - start_ms) == 72 * 3600 * 1000


def test_rolling_window_does_not_grow_with_run_age() -> None:
    """48h and 72h runs with window=24h analyze the same span length."""
    from datetime import UTC, datetime, timedelta

    spans = []
    end_dt = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    for hours_ago in (48, 72):
        start_dt = end_dt - timedelta(hours=hours_ago)
        state = RunState(
            run_id="r1",
            started_at=start_dt.isoformat(),
            status="running",
            last_successful_flush=end_dt.isoformat(),
            last_durable_event_ms=int(end_dt.timestamp() * 1000),
        )
        start_ms, end_ms, _, _ = _analysis_window_ms(
            state,
            execution_start_ms=int(end_dt.timestamp() * 1000) + 60_000,
            window_hours=24.0,
        )
        spans.append(end_ms - start_ms)
    assert spans[0] == spans[1] == 24 * 3600 * 1000
