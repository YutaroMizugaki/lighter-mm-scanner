"""Live-dashboard freeze: sync cadence + failure status publish."""

from __future__ import annotations

from lighter_mm.cloud.dashboard_data import collector_status_label
from lighter_mm.storage.state import RunState, now_iso


def test_collector_status_becomes_offline_when_flush_old() -> None:
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    state = RunState(
        run_id="r1",
        started_at=old,
        status="running",
        last_successful_flush=old,
        samples_written=1000,
    )
    label = collector_status_label(state, ok_minutes=20, warn_minutes=40, markets_analyzed=0)
    assert label == "OFFLINE"


def test_publish_failure_status_shape() -> None:
    # Smoke: helper exists and builds a payload the dashboard can read.
    from unittest.mock import MagicMock

    from lighter_mm.collector import CollectorApp
    from lighter_mm.config import Settings

    app = object.__new__(CollectorApp)
    app.run_id = "abc"
    app.settings = Settings()
    app.state = RunState(run_id="abc", started_at=now_iso(), status="running", samples_written=10)
    app.sync = MagicMock()
    app.sync.public_key.side_effect = lambda name: f"lighter-mm/public/{name}"
    app.backend = MagicMock()
    app._write_state = lambda: None  # type: ignore[method-assign]

    CollectorApp._publish_public_failure_status(app, "boom")
    args, kwargs = app.backend.upload_json.call_args
    assert args[0] == "lighter-mm/public/latest.json"
    assert args[1]["status"] == "ERROR"
    assert args[1]["analysis_error"] == "boom"
    assert kwargs.get("public") is True
