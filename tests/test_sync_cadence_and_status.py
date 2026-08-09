"""Live-dashboard freeze: sync cadence + failure status publish."""

from __future__ import annotations

from lighter_mm.cloud.dashboard_data import collector_status_label
from lighter_mm.collector import CollectorApp
from lighter_mm.storage.state import RunState


def test_collector_status_becomes_offline_when_event_old() -> None:
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    old_ms = int((datetime.now(UTC) - timedelta(minutes=45)).timestamp() * 1000)
    state = RunState(
        run_id="r1",
        started_at=old,
        status="running",
        last_successful_flush=old,
        last_durable_event_ms=old_ms,
        samples_written=1000,
    )
    label = collector_status_label(state, ok_minutes=20, warn_minutes=40)
    assert label == "OFFLINE"


def test_publish_failure_status_removed() -> None:
    assert not hasattr(CollectorApp, "_publish_public_failure_status")
