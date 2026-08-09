"""Parquet flush hardening: no silent data loss, finalized row counting."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq

from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.parquet_validation import validate_parquet_file
from tests.helpers import enrich_book_row


def _book_row(ts: int) -> dict:
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


def test_rows_written_only_after_finalize(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60)
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))
    assert store.samples_written == 0
    store.close()
    assert store.samples_written == 1
    parts = list((tmp_path / "book_samples").glob("date=*/hour=*/*.parquet"))
    assert len(parts) == 1


def test_flush_write_failure_retains_buffer(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=100, flush_seconds=60)
    ts = int(time.time() * 1000)
    store.book._buffer.append(_book_row(ts))
    with patch.object(pq.ParquetWriter, "write_table", side_effect=RuntimeError("boom")):
        store.book._flush_unlocked()
    assert len(store.book._buffer) == 1
    assert store.samples_written == 0


def test_validation_failure_does_not_increment_rows_written(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=100, flush_seconds=60)
    ts = int(time.time() * 1000)
    store.book._buffer.append(_book_row(ts))
    store.book._flush_unlocked()
    store.book._close_writer_unlocked()
    assert store.samples_written == 1
    # Simulate corrupt tmp on next close attempt by reopening and breaking finalize.
    store.book._buffer.append(_book_row(ts + 1000))
    store.book._flush_unlocked()
    assert store.book._tmp_path is not None
    store.book._writer.close()
    store.book._writer = None
    store.book._tmp_path.write_bytes(b"broken")
    with patch(
        "lighter_mm.storage.parquet_store.validate_parquet_file",
        return_value=(False, "broken"),
    ):
        store.book._finalize_parquet_unlocked(
            store.book._tmp_path,
            store.book._current_path,
            rows_in_writer=1,
        )
    assert store.samples_written == 1


def test_rename_failure_does_not_increment_rows_written(tmp_path: Path) -> None:
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


def test_multi_partition_partial_failure_keeps_failed_rows(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=99, flush_seconds=60)
    base_ts = int(time.time() * 1000)
    store.book._buffer.append(_book_row(base_ts))
    store.book._buffer.append(_book_row(base_ts + 3_600_000))
    calls = {"n": 0}
    real_write = pq.ParquetWriter.write_table

    def flaky_write(self, table, *args, **kwargs):  # noqa: ANN001, ANN002
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second partition failed")
        return real_write(self, table, *args, **kwargs)

    with patch.object(pq.ParquetWriter, "write_table", flaky_write):
        store.book._flush_unlocked()
    assert len(store.book._buffer) == 1
    # First partition rotates writer and finalizes; second partition rows stay buffered.
    assert store.samples_written == 1


def test_retry_after_failure_no_duplicate_finalize(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10, 25], flush_rows=100, flush_seconds=60)
    ts = int(time.time() * 1000)
    store.book._buffer.append(_book_row(ts))
    with patch.object(pq.ParquetWriter, "write_table", side_effect=RuntimeError("boom")):
        store.book._flush_unlocked()
    assert len(store.book._buffer) == 1
    store.book._buffer.append(_book_row(ts + 1000))
    store.close()
    assert store.samples_written == 2
    parts = list((tmp_path / "book_samples").glob("date=*/hour=*/*.parquet"))
    assert len(parts) == 1
    ok, _ = validate_parquet_file(parts[0])
    assert ok
