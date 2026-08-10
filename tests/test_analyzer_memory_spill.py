"""Analyzer DuckDB memory spill / materialization guards against Cloud Run OOM."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.analytics.parquet_source import AnalysisSources, _connect
from lighter_mm.config import Settings


def test_duckdb_connect_uses_file_backed_db_with_temp_directory(tmp_path: Path) -> None:
    con = _connect(tmp_path, memory_limit="256MB", threads=1)
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
        assert rows, "expected at least one database"
        # file path present (not ':memory:')
        paths = [str(r[2]) for r in rows]
        assert any(p and p != "" and p != ":memory:" for p in paths), paths
        tmp_dir = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        assert tmp_dir
        assert "lighter-mm-duckdb-" in str(tmp_dir) or Path(tmp_dir).exists()
        mem = str(con.execute("SELECT current_setting('memory_limit')").fetchone()[0])
        # DuckDB normalizes 256MB ≈ 244.1 MiB
        assert "MiB" in mem or "MB" in mem or "256" in mem
    finally:
        con.close()


def test_analyze_range_materializes_book_tables(tmp_path: Path) -> None:
    books = tmp_path / "books"
    trades = tmp_path / "trades"
    markouts = tmp_path / "markouts"
    for d in (books, trades, markouts):
        (d / "date=2026-08-10" / "hour=00").mkdir(parents=True)

    ts = 1_700_000_000_000
    table = pa.table(
        {
            "timestamp_ms": pa.array([ts, ts + 5000], type=pa.int64()),
            "market_id": pa.array([1, 1], type=pa.int32()),
            "symbol": pa.array(["BTC", "BTC"], type=pa.string()),
            "best_bid": pa.array([100.0, 100.1], type=pa.float64()),
            "best_ask": pa.array([100.2, 100.3], type=pa.float64()),
            "mid": pa.array([100.1, 100.2], type=pa.float64()),
            "spread_bps": pa.array([20.0, 20.0], type=pa.float64()),
            "best_bid_size_usd": pa.array([500.0, 500.0], type=pa.float64()),
            "best_ask_size_usd": pa.array([500.0, 500.0], type=pa.float64()),
            "two_sided_depth_5bps_usd": pa.array([1000.0, 1000.0], type=pa.float64()),
            "two_sided_depth_10bps_usd": pa.array([2000.0, 2000.0], type=pa.float64()),
            "two_sided_depth_25bps_usd": pa.array([3000.0, 3000.0], type=pa.float64()),
            "is_stale": pa.array([False, False], type=pa.bool_()),
            "is_usable": pa.array([True, True], type=pa.bool_()),
            "is_inactive": pa.array([False, False], type=pa.bool_()),
            "book_update_age_ms": pa.array([0, 0], type=pa.int64()),
            "current_funding_rate": pa.array([0.0, 0.0], type=pa.float64()),
            "funding_rate": pa.array([0.0, 0.0], type=pa.float64()),
            "open_interest": pa.array([1.0, 1.0], type=pa.float64()),
            "daily_quote_token_volume": pa.array([1.0, 1.0], type=pa.float64()),
        }
    )
    pq.write_table(table, books / "date=2026-08-10" / "hour=00" / "part.parquet")

    settings = Settings(data_dir=tmp_path, book_sample_interval_seconds=5)
    result = analyze_range(
        settings,
        start_ms=ts - 1000,
        end_ms=ts + 60_000,
        sources=AnalysisSources(books=books, trades=trades, markouts=markouts),
        duckdb_memory_limit="256MB",
        duckdb_threads=1,
        read_only=True,
    )
    assert result.get("book_row_count", 0) >= 1
    assert not result.get("error")


def test_volatility_sql_uses_join_not_correlated_subquery() -> None:
    import inspect

    from lighter_mm.analytics import book_metrics

    src = inspect.getsource(book_metrics._volatility_sql)
    assert "INNER JOIN book_observed" in src
    # Ensure the old scalar subquery pattern is gone.
    assert "ORDER BY b.timestamp_ms ASC\n                    LIMIT 1" not in src
