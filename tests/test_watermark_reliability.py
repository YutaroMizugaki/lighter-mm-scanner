"""Regression tests for durable event watermark vs sync time separation."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.cloud.analyzer_target import _analysis_window_ms, _durable_watermark_ms
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.cloud.health import (
    _ws_connected,
    _ws_degraded,
    build_collector_status_payload,
    latest_data_timestamp_iso,
)
from lighter_mm.cloud.sync import DurableSync, PartialParquetUploadError, UploadedParquet
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_store import ParquetStore, _book_schema
from lighter_mm.storage.parquet_validation import validate_parquet_file
from lighter_mm.storage.state import RunState, now_iso
from tests.helpers import enrich_book_row


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _book_row(ts: int) -> dict:
    return enrich_book_row(
        {
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
        }
    )


def _make_collector_app(tmp_path: Path) -> CollectorApp:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    backend = LocalStorageBackend(tmp_path / "remote")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = backend
    app.run_id = "run1"
    app.state = RunState(run_id="run1", started_at=now_iso(), status="running")
    app.state.last_durable_event_ms = _ms("2026-01-01T14:10:00+00:00")
    app.sync = DurableSync(backend, run_id="run1")
    app.store = ParquetStore(settings.data_dir, depth_levels=[5, 10, 25], flush_rows=100)
    app._consecutive_sync_failures = 0
    app._last_sync_error = None
    app._last_sync_attempt_at = None
    app._lost_leadership = False
    app._durable_write_lock = threading.RLock()
    app.lock = type("L", (), {"renew": lambda *a, **k: True})()
    app._write_state = lambda: None  # noqa: E731
    app._require_leadership = lambda **kwargs: None  # noqa: E731
    return app


def test_sync_without_new_data_does_not_advance_watermark(tmp_path: Path) -> None:
    app = _make_collector_app(tmp_path)
    before = app.state.last_durable_event_ms
    with patch.object(app.sync, "upload_new_parquets", return_value=[]):
        with patch.object(app, "_publish_collector_status"):
            with patch.object(app.backend, "upload_json"):
                app._sync_only(final=False)
    assert app.state.last_successful_flush is not None
    assert app.state.last_durable_event_ms == before


def test_uploaded_parquet_advances_event_watermark(tmp_path: Path) -> None:
    app = _make_collector_app(tmp_path)
    event_ms = _ms("2026-01-01T14:20:00+00:00")
    uploaded = [
        UploadedParquet(
            Path("x.parquet"),
            "remote/x.parquet",
            100,
            max_event_timestamp_ms=event_ms,
        )
    ]
    with patch.object(app.sync, "upload_new_parquets", return_value=uploaded):
        with patch.object(app, "_publish_collector_status"):
            with patch.object(app.backend, "upload_json"):
                app._sync_only(final=False)
    assert app.state.last_durable_event_ms == event_ms


def test_old_uploaded_parquet_does_not_move_watermark_backward(tmp_path: Path) -> None:
    app = _make_collector_app(tmp_path)
    old_event_ms = _ms("2026-01-01T13:50:00+00:00")
    uploaded = [
        UploadedParquet(
            Path("x.parquet"),
            "remote/x.parquet",
            100,
            max_event_timestamp_ms=old_event_ms,
        )
    ]
    with patch.object(app.sync, "upload_new_parquets", return_value=uploaded):
        with patch.object(app, "_publish_collector_status"):
            with patch.object(app.backend, "upload_json"):
                app._sync_only(final=False)
    assert app.state.last_durable_event_ms == _ms("2026-01-01T14:10:00+00:00")


def test_analyzer_uses_event_watermark_not_sync_time() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        status="running",
        last_successful_flush="2026-01-01T00:19:00+00:00",
        last_durable_event_ms=_ms("2026-01-01T00:15:00+00:00"),
    )
    _, end_ms, _, durable_ms = _analysis_window_ms(
        state, execution_start_ms=_ms("2026-01-01T00:19:00+00:00")
    )
    assert end_ms == _ms("2026-01-01T00:15:00+00:00")
    assert durable_ms == _ms("2026-01-01T00:15:00+00:00")
    assert _durable_watermark_ms(state) == _ms("2026-01-01T00:15:00+00:00")


def test_last_data_excludes_sync_timestamp() -> None:
    flush = datetime(2026, 8, 9, 14, 10, 0, tzinfo=UTC)
    event_ms = _ms("2026-08-09T14:00:00+00:00")
    iso = latest_data_timestamp_iso(
        last_durable_event_ms=event_ms,
    )
    assert iso is not None
    parsed = datetime.fromisoformat(iso)
    assert parsed < flush


def test_ws_zero_zero_not_connected() -> None:
    assert _ws_connected({"connected_shards": 0, "total_shards": 0}) is False
    warnings = _ws_degraded({"connected_shards": 0, "total_shards": 0, "planned_channels": 0})
    assert any("No WebSocket shards planned." in w for w in warnings)
    assert any("No WebSocket subscriptions planned." in w for w in warnings)


def test_collector_and_analysis_status_are_independent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        last_durable_event_ms=int(time.time() * 1000) - 60_000,
        samples_written=100,
        markets=[1],
    )
    payload = build_dashboard_payload(
        settings,
        hours=1,
        state=state,
        analysis_result={
            "scored": [],
            "avoid": [],
            "error": "boom",
            "parquet_health": {"status": "healthy", "valid_parquet_files": 1, "corrupt_parquet_files": 0},
        },
    )
    latest = payload["latest"]
    assert latest["collector_status"] == "COLLECTING"
    assert latest["analysis_status"] == "ERROR"
    assert latest["diagnostics"]["collector"]["status"] == "COLLECTING"
    assert latest["diagnostics"]["analysis"]["status"] == "ERROR"


def test_stale_book_warning_text_matches_condition() -> None:
    settings = Settings(book_sample_interval_seconds=5.0)
    stale_ms = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp() * 1000)
    payload = build_collector_status_payload(
        RunState(run_id="r1", started_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()),
        settings=settings,
        last_book_row_at_ms=stale_ms,
        last_book_sample_at_ms=None,
    )
    warnings = payload["health_warnings"]
    assert any("stale" in w.lower() for w in warnings)
    assert not any("fresh" in w.lower() for w in warnings)


def test_parquet_data_corruption_is_detected_or_isolated(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    good_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    good_dir.mkdir(parents=True)
    ts = int(time.time() * 1000)
    pq.write_table(
        pa.Table.from_pylist([_book_row(ts)], schema=_book_schema([5, 10, 25])),
        good_dir / "good.parquet",
    )
    bad_dir = tmp_path / "book_samples/date=2026-08-09/hour=11"
    bad_dir.mkdir(parents=True)
    bad = bad_dir / "bad.parquet"
    bad.write_bytes(b"PAR1" + b"\x00" * 100)
    ok_meta, _ = validate_parquet_file(good_dir / "good.parquet")
    ok_bad, _ = validate_parquet_file(bad)
    assert ok_meta
    assert not ok_bad
    result = analyze_range(settings, start_ms=ts - 60_000, end_ms=ts + 60_000)
    assert result.get("error") is None
    assert result["parquet_health"]["status"] in {"degraded", "healthy"}


def test_duckdb_bad_file_fallback_continues_with_good_files(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    good_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    good_dir.mkdir(parents=True)
    ts = int(time.time() * 1000)
    pq.write_table(
        pa.Table.from_pylist([_book_row(ts)], schema=_book_schema([5, 10, 25])),
        good_dir / "good.parquet",
    )
    bad_dir = tmp_path / "book_samples/date=2026-08-09/hour=11"
    bad_dir.mkdir(parents=True)
    bad = bad_dir / "bad.parquet"
    schema = _book_schema([5, 10, 25])
    pq.write_table(pa.Table.from_pylist([_book_row(ts)], schema=schema), bad)
    with open(bad, "r+b") as fh:
        fh.seek(200)
        fh.write(b"\xff" * 64)
    result = analyze_range(settings, start_ms=ts - 60_000, end_ms=ts + 60_000)
    assert result.get("error") is None or result["parquet_health"]["status"] == "degraded"
    if result.get("error") is None:
        assert len(result["scored"]) >= 1


def test_parquet_rename_failure_preserves_retryable_data(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=100, flush_seconds=60)
    ts = int(time.time() * 1000)
    store.book._buffer.append(_book_row(ts))
    store.book._flush_unlocked()
    store.book._writer.close()
    store.book._writer = None
    tmp_path_file = store.book._tmp_path
    final_path = store.book._current_path
    assert tmp_path_file is not None and final_path is not None
    with patch("lighter_mm.storage.parquet_store.os.replace", side_effect=OSError("rename failed")):
        ok = store.book._finalize_parquet_unlocked(tmp_path_file, final_path, rows_in_writer=1)
    assert not ok
    assert store.samples_written == 0
    assert tmp_path_file.exists()
    assert not final_path.exists()


def test_partial_parquet_upload_advances_watermark(tmp_path: Path) -> None:
    app = _make_collector_app(tmp_path)
    data_root = app.settings.data_dir
    schema = _book_schema([5, 10, 25])
    ts_new = _ms("2026-01-01T14:30:00+00:00")
    ts_old = _ms("2026-01-01T14:25:00+00:00")
    p1 = data_root / "book_samples/date=2026-01-01/hour=14/part-a.parquet"
    p2 = data_root / "book_samples/date=2026-01-01/hour=14/part-b.parquet"
    p1.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([_book_row(ts_new)], schema=schema), p1)
    pq.write_table(pa.Table.from_pylist([_book_row(ts_old)], schema=schema), p2)

    class FailSecond:
        def __init__(self, inner: LocalStorageBackend) -> None:
            self.inner = inner
            self.n = 0

        def upload_file(self, local_path, remote_key, **kwargs):  # noqa: ANN001
            self.n += 1
            if self.n >= 2:
                raise RuntimeError("second failed")
            return self.inner.upload_file(local_path, remote_key, **kwargs)

        def __getattr__(self, name: str):  # noqa: ANN001
            return getattr(self.inner, name)

    app.sync.backend = FailSecond(app.backend)
    before = app.state.last_durable_event_ms
    with patch.object(app.store, "take_closed_paths", return_value=[p1, p2]):
        with patch.object(app.store, "maybe_flush"):
            with patch.object(app.store, "rotate_all"):
                with patch.object(app, "_publish_collector_status"):
                    with pytest.raises(PartialParquetUploadError):
                        app._sync_only(final=False)
    assert app.state.last_durable_event_ms == ts_new
    assert app.state.last_durable_event_ms > before
    assert not p1.exists()
    assert p2.exists()
    assert app._consecutive_sync_failures == 1
