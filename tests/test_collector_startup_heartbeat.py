"""Collector resume/startup must heartbeat before heavy DuckDB analysis."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.cloud.sync import DurableSync
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_store import _book_schema
from lighter_mm.storage.state import RunState, now_iso
from tests.helpers import enrich_book_row


def _minimal_book_row(ts: int) -> dict:
    return enrich_book_row({
        "timestamp_ms": ts,
        "market_id": 1,
        "symbol": "ETH",
        "best_bid": 100.0,
        "best_ask": 100.1,
        "mid": 100.05,
        "spread_absolute": 0.1,
        "spread_bps": 10.0,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 100.0,
        "best_ask_size_usd": 100.1,
        "is_stale": False,
        "is_usable": True,
        "nonce": 1,
        "index_price": None,
        "mark_price": None,
        "stats_mid_price": None,
        "open_interest": None,
        "last_trade_price": None,
        "current_funding_rate": None,
        "funding_rate": None,
        "daily_base_token_volume": None,
        "daily_quote_token_volume": None,
        "daily_price_low": None,
        "daily_price_high": None,
        "daily_price_change": None,
        "bid_depth_5bps_usd": 50.0,
        "ask_depth_5bps_usd": 50.0,
        "two_sided_depth_5bps_usd": 50.0,
        "bid_depth_10bps_usd": 200.0,
        "ask_depth_10bps_usd": 200.0,
        "two_sided_depth_10bps_usd": 200.0,
        "bid_depth_25bps_usd": 400.0,
        "ask_depth_25bps_usd": 400.0,
        "two_sided_depth_25bps_usd": 400.0,
    })


def test_hydrate_invokes_progress_callback(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path / "remote")
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")

    schema = _book_schema([5, 10, 25])
    ts = int(time.time() * 1000)
    for i in range(3):
        local = tmp_path / f"part{i}.parquet"
        table = pa.Table.from_pylist([_minimal_book_row(ts + i)], schema=schema)
        pq.write_table(table, local)
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
        last_successful_flush=now_iso(),
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
    app._last_usable_book_sample_ts = None
    app._last_book_row_written_ts = None
    app._trades_without_reference_mid = 0
    app._last_sync_error = None
    app._consecutive_sync_failures = 0
    app._last_sync_attempt_at = None
    app.store = MagicMock(samples_written=0, trades_written=0, markouts_written=0)
    app.counters = MagicMock(
        dropped_connections=0, book_resyncs=0, nonce_gaps=0
    )
    app._ws = None
    app._lost_leadership = False
    app.lock = MagicMock()
    app.lock.renew.return_value = True

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

    latest = app.backend.download_json("lighter-mm/public/collector_status.json")
    assert latest is not None
    assert latest["git_sha"] == "deadbeef"
    assert latest["status"] == "COLLECTING"
    assert latest["samples_written"] == 19065
