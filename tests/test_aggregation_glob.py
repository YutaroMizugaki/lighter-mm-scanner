"""Regression: aggregation must find date=/hour= Parquet layout."""

from __future__ import annotations

import time
from pathlib import Path

from lighter_mm.analytics.aggregation import _glob_or_none, analyze_window
from lighter_mm.config import Settings
from lighter_mm.storage.parquet_store import ParquetStore


def test_glob_or_none_prefers_hour_partitions(tmp_path: Path) -> None:
    nested = tmp_path / "book_samples" / "date=2026-08-09" / "hour=10"
    nested.mkdir(parents=True)
    (nested / "part-1.parquet").write_bytes(b"x")

    got = _glob_or_none(tmp_path / "book_samples")
    assert got is not None
    assert got.endswith("date=*/hour=*/*.parquet")


def test_glob_or_none_falls_back_to_flat_date(tmp_path: Path) -> None:
    flat = tmp_path / "book_samples" / "date=2026-08-09"
    flat.mkdir(parents=True)
    (flat / "part-1.parquet").write_bytes(b"x")

    got = _glob_or_none(tmp_path / "book_samples")
    assert got is not None
    assert got.endswith("date=*/*.parquet")


def test_analyze_window_reads_hour_partitioned_books(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(
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
    store.close()

    assert list((tmp_path / "book_samples").glob("date=*/hour=*/*.parquet"))

    result = analyze_window(settings, hours=1.0)
    assert result.get("error") != "no book_samples yet"
    assert len(result["markets"]) >= 1
    assert result["markets"][0]["symbol"] == "ETH"
    assert len(result["scored"]) >= 1
