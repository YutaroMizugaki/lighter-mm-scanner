"""Atomic JSON write and latest data timestamp helpers."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.cloud.health import latest_data_timestamp_iso
from lighter_mm.storage.json_atomic import atomic_write_json, atomic_write_text, dumps_validated_json
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_store import _book_schema
from tests.helpers import enrich_book_row


def test_dumps_validated_json_round_trip() -> None:
    payload = {"status": "OK", "count": 3, "nested": {"a": 1}}
    data = dumps_validated_json(payload)
    assert json.loads(data) == payload


def test_atomic_write_json_no_partial_final_file(tmp_path: Path) -> None:
    dest = tmp_path / "analysis_status.json"
    atomic_write_json(dest, {"status": "RUNNING"})
    assert dest.exists()
    assert not (tmp_path / "analysis_status.json.tmp").exists()
    assert json.loads(dest.read_text())["status"] == "RUNNING"


def test_atomic_write_text_replaces_existing(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    atomic_write_text(dest, '{"v":1}')
    atomic_write_text(dest, '{"v":2}')
    assert json.loads(dest.read_text())["v"] == 2


def test_local_backend_upload_json_is_atomic(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    be.upload_json("lighter-mm/public/analysis_status.json", {"status": "OK"}, public=True)
    path = tmp_path / "remote" / "lighter-mm/public/analysis_status.json"
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_latest_data_timestamp_prefers_event_over_flush() -> None:
    event_ms = int(datetime.now(UTC).timestamp() * 1000) - 3_600_000
    iso = latest_data_timestamp_iso(last_durable_event_ms=event_ms)
    assert iso is not None
    parsed = datetime.fromisoformat(iso)
    assert parsed < datetime.now(UTC) - timedelta(minutes=30)


def test_latest_data_timestamp_from_event_only() -> None:
    event = datetime(2026, 8, 9, 14, 0, 0, tzinfo=UTC)
    iso = latest_data_timestamp_iso(last_durable_event_ms=int(event.timestamp() * 1000))
    assert iso == event.isoformat()


def test_upload_skips_corrupt_parquet_before_remote(tmp_path: Path) -> None:
    from lighter_mm.cloud.sync import DurableSync

    be = LocalStorageBackend(tmp_path / "remote")
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    data_root = tmp_path / "data"
    bad = data_root / "book_samples/date=2026-08-09/hour=14/part-bad.parquet"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not parquet")
    good = data_root / "book_samples/date=2026-08-09/hour=10/part-good.parquet"
    good.parent.mkdir(parents=True, exist_ok=True)
    schema = _book_schema([5, 10, 25])
    ts = int(time.time() * 1000)
    row = enrich_book_row(
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
    pq.write_table(pa.Table.from_pylist([row], schema=schema), good)
    uploaded = sync.upload_new_parquets(data_root, paths=[bad, good])
    assert len(uploaded) == 1
    assert bad.exists()
    assert not be.exists(
        "lighter-mm/runs/run1/books/date=2026-08-09/hour=14/part-bad.parquet"
    )
