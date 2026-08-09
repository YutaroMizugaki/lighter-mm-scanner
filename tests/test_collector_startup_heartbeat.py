"""Collector resume/startup must heartbeat before heavy DuckDB analysis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from lighter_mm.cloud.sync import DurableSync
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.state import RunState, now_iso


def test_hydrate_invokes_progress_callback(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path / "remote")
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")

    # Seed a few remote parquet objects.
    for i in range(3):
        local = tmp_path / f"part{i}.parquet"
        local.write_bytes(b"PAR1" + bytes([i]))
        be.upload_file(
            local,
            f"lighter-mm/runs/run1/books/date=2026-08-09/hour=0{i}/part.parquet",
        )

    seen: list[tuple[int, int]] = []
    restored = sync.hydrate_run_parquets(
        data_root,
        on_progress=lambda scanned, restored_n: seen.append((scanned, restored_n)),
        progress_every=2,
    )
    assert len(restored) == 3
    assert seen, "progress callback should run"
    assert seen[-1][0] == 3
    assert (data_root / "book_samples/date=2026-08-09/hour=00/part.parquet").exists()


def test_heartbeat_running_updates_active_run_and_latest(tmp_path: Path) -> None:
    settings = Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        git_sha="deadbeef",
    )
    settings.data_dir.mkdir(parents=True)
    settings.reports_dir.mkdir(parents=True)

    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.run_id = "abc123"
    app.state = RunState(
        run_id="abc123",
        started_at=now_iso(),
        status="running",
        observation_target_hours=72.0,
        samples_written=19065,
        last_successful_flush="2026-08-09T11:57:47+00:00",
        git_sha="oldsha",
        markets=[1, 2, 3],
    )
    app.sync = DurableSync(app.backend, run_id=app.run_id, gcs_prefix="lighter-mm")
    app.holder_id = "holder1"
    app._samples_baseline = 19065
    app._trades_baseline = 0
    app._markouts_baseline = 0
    app._drops_baseline = 0
    app._resyncs_baseline = 0
    app._gaps_baseline = 0
    app._deployment_gaps = 1
    app._last_trade_ts = None
    app.store = MagicMock(samples_written=0, trades_written=0, markouts_written=0)
    app.counters = MagicMock(
        dropped_connections=0, book_resyncs=0, nonce_gaps=0
    )
    app._ws = None

    app._heartbeat_running("leader elected; starting collector")

    active = app.backend.download_json("lighter-mm/state/active_run.json")
    assert active is not None
    assert active["run_id"] == "abc123"
    assert active["status"] == "running"
    assert active["git_sha"] == "deadbeef"
    assert "leader elected" in active["note"]

    state = app.backend.download_json("lighter-mm/runs/abc123/state/state.json")
    assert state is not None
    assert state["git_sha"] == "deadbeef"
    assert state["samples_written"] == 19065

    latest = app.backend.download_json("lighter-mm/public/latest.json")
    assert latest is not None
    assert latest["git_sha"] == "deadbeef"
    assert latest["status"] == "COLLECTING"
    # Heartbeat must not fake a successful flush.
    assert latest["last_successful_flush"] == "2026-08-09T11:57:47+00:00"
    assert latest["samples_written"] == 19065
    assert latest["health_warnings"]
