"""Regression tests for parquet_max_timestamp_ms watermark extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.cloud.analyzer_target import (
    AnalysisSources,
    _durable_watermark_ms,
)
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_validation import parquet_max_timestamp_ms
from lighter_mm.storage.state import RunState, now_iso
from tests.test_collector_analyzer_split import _write_book_parquet
from tests.test_watermark_reliability import _make_collector_app


def _trade_schema() -> pa.Schema:
    return pa.schema(
        [
            ("timestamp_ms", pa.int64()),
            ("market_id", pa.int32()),
            ("symbol", pa.string()),
            ("trade_id", pa.int64()),
            ("price", pa.float64()),
            ("size", pa.float64()),
            ("usd_amount", pa.float64()),
            ("is_maker_ask", pa.bool_()),
            ("taker_is_buy", pa.bool_()),
            ("type", pa.string()),
            ("reference_mid", pa.float64()),
        ]
    )


def _write_trades_parquet(path: Path, timestamps: list[int | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp_ms": ts,
            "market_id": 1,
            "symbol": "ETH",
            "trade_id": i,
            "price": 100.0,
            "size": 1.0,
            "usd_amount": 100.0,
            "is_maker_ask": True,
            "taker_is_buy": False,
            "type": "trade",
            "reference_mid": 100.0,
        }
        for i, ts in enumerate(timestamps)
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=_trade_schema()), path)


def test_normal_parquet_max_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    _write_trades_parquet(path, [1000, 2000, 3500, 2500])
    assert parquet_max_timestamp_ms(path) == 3500


def test_multi_row_group_parquet_max_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "multi.parquet"
    schema = _trade_schema()
    writer = pq.ParquetWriter(path, schema, write_statistics=True)
    writer.write_table(
        pa.Table.from_pylist(
            [
                {
                    "timestamp_ms": 1000,
                    "market_id": 1,
                    "symbol": "ETH",
                    "trade_id": 1,
                    "price": 1.0,
                    "size": 1.0,
                    "usd_amount": 1.0,
                    "is_maker_ask": True,
                    "taker_is_buy": False,
                    "type": "trade",
                    "reference_mid": 1.0,
                }
            ],
            schema=schema,
        )
    )
    writer.write_table(
        pa.Table.from_pylist(
            [
                {
                    "timestamp_ms": 9000,
                    "market_id": 1,
                    "symbol": "ETH",
                    "trade_id": 2,
                    "price": 1.0,
                    "size": 1.0,
                    "usd_amount": 1.0,
                    "is_maker_ask": True,
                    "taker_is_buy": False,
                    "type": "trade",
                    "reference_mid": 1.0,
                }
            ],
            schema=schema,
        )
    )
    writer.close()
    assert parquet_max_timestamp_ms(path) == 9000


def test_missing_timestamp_column_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "no_ts.parquet"
    pq.write_table(pa.table({"market_id": [1], "symbol": ["ETH"]}), path)
    assert parquet_max_timestamp_ms(path) is None


def test_all_null_or_empty_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty.parquet"
    pq.write_table(pa.table({"timestamp_ms": pa.array([], type=pa.int64())}), empty)
    assert parquet_max_timestamp_ms(empty) is None

    nulls = tmp_path / "nulls.parquet"
    _write_trades_parquet(nulls, [None, None])
    assert parquet_max_timestamp_ms(nulls) is None


def test_fallback_when_row_group_statistics_missing(tmp_path: Path) -> None:
    path = tmp_path / "no_stats.parquet"
    _write_trades_parquet(path, [1000, 5000, 3000])
    # Re-write without statistics to force per-row-group data reads.
    table = pq.read_table(path)
    pq.write_table(table, path, write_statistics=False)
    assert parquet_max_timestamp_ms(path) == 5000


def test_corrupt_parquet_returns_none_without_crash(tmp_path: Path) -> None:
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"PAR1" + b"\x00" * 32)
    assert parquet_max_timestamp_ms(bad) is None


def test_analyzer_fallback_from_durable_parquet(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    run_id = "run1"
    trades_root = mount / "lighter-mm" / "runs" / run_id / "trades"
    part = trades_root / "date=2026-08-10/hour=04/part.parquet"
    _write_trades_parquet(part, [1000, 4200, 3100])
    sources = AnalysisSources(
        books=mount / "lighter-mm" / "runs" / run_id / "books",
        trades=trades_root,
        markouts=mount / "lighter-mm" / "runs" / run_id / "markouts",
    )
    state = RunState(run_id=run_id, started_at=now_iso(), status="running")
    assert state.last_durable_event_ms is None
    assert _durable_watermark_ms(state, sources=sources) == 4200


def test_collector_sync_updates_watermark_from_uploaded_parquet(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path / "remote")
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    ts = 1_700_000_000_000
    local = data_root / "book_samples/date=2026-08-09/hour=10/part-x.parquet"
    _write_book_parquet(local, ts)

    app = _make_collector_app(tmp_path)
    app.settings = Settings(data_dir=data_root, reports_dir=tmp_path / "reports")
    app.sync = sync
    app.state.last_durable_event_ms = None
    app.store.take_closed_paths = lambda: [local]  # type: ignore[method-assign]
    app.store.maybe_flush = lambda: None  # type: ignore[method-assign]
    app.store.rotate_all = lambda: None  # type: ignore[method-assign]

    with patch.object(app, "_publish_collector_status"):
        with patch.object(app.backend, "upload_json"):
            app._sync_only(final=False)

    assert app.state.last_durable_event_ms == ts
    assert not local.exists()


def test_upload_with_timestamp_must_not_leave_watermark_none(tmp_path: Path) -> None:
    """Upload success with timestamp_ms data must advance last_durable_event_ms."""
    be = LocalStorageBackend(tmp_path / "remote")
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    ts = 1_700_000_123_000
    local = data_root / "trades/date=2026-08-10/hour=04/part.parquet"
    _write_trades_parquet(local, [ts - 1000, ts])

    app = _make_collector_app(tmp_path)
    app.settings = Settings(data_dir=data_root, reports_dir=tmp_path / "reports")
    app.sync = sync
    app.state.last_durable_event_ms = None
    app.store.take_closed_paths = lambda: [local]  # type: ignore[method-assign]
    app.store.maybe_flush = lambda: None  # type: ignore[method-assign]
    app.store.rotate_all = lambda: None  # type: ignore[method-assign]

    with patch.object(app, "_publish_collector_status"):
        with patch.object(app.backend, "upload_json"):
            app._sync_only(final=False)

    assert app.state.last_successful_flush is not None
    assert app.state.last_durable_event_ms == ts
    assert not local.exists()


def test_has_max_attribute_not_used() -> None:
    """Guard against reintroducing the nonexistent PyArrow Statistics.has_max."""
    import pyarrow.parquet as pq_mod

    schema = _trade_schema()
    table = pa.Table.from_pylist(
        [{"timestamp_ms": 1, "market_id": 1, "symbol": "ETH", "trade_id": 1,
          "price": 1.0, "size": 1.0, "usd_amount": 1.0, "is_maker_ask": True,
          "taker_is_buy": False, "type": "trade", "reference_mid": 1.0}],
        schema=schema,
    )
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".parquet") as fh:
        pq_mod.write_table(table, fh.name)
        pf = pq_mod.ParquetFile(fh.name)
        stats = pf.metadata.row_group(0).column(0).statistics
        assert stats is not None
        assert not hasattr(stats, "has_max")
        assert stats.has_min_max
