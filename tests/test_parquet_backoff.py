"""Parquet rotation and reconnect backoff helpers."""

from __future__ import annotations

import time
from pathlib import Path

from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.util import backoff_delay
from tests.helpers import enrich_book_row


def test_backoff_increases_with_jitter_cap() -> None:
    d0 = backoff_delay(0, base=1.0, maximum=10.0)
    d3 = backoff_delay(3, base=1.0, maximum=10.0)
    assert 1.0 <= d0 <= 1.25 + 1e-6
    assert d3 <= 10.0
    assert d3 >= d0 or d3 == 10.0


def test_parquet_rotation_and_flush(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=2, flush_seconds=60)
    ts = int(time.time() * 1000)
    for i in range(3):
        row = {
            "timestamp_ms": ts + i * 1000,
            "market_id": 0,
            "symbol": "ETH",
            "best_bid": 1.0,
            "best_ask": 1.1,
            "mid": 1.05,
            "spread_absolute": 0.1,
            "spread_bps": 10.0,
            "best_bid_size_base": 1.0,
            "best_ask_size_base": 1.0,
            "best_bid_size_usd": 1.0,
            "best_ask_size_usd": 1.1,
            "is_stale": False,
            "nonce": i,
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
            "bid_depth_5bps_usd": 1.0,
            "ask_depth_5bps_usd": 1.0,
            "two_sided_depth_5bps_usd": 1.0,
            "bid_depth_10bps_usd": 2.0,
            "ask_depth_10bps_usd": 2.0,
            "two_sided_depth_10bps_usd": 2.0,
        }
        store.write_book(enrich_book_row(row))
    store.close()
    parts = list((tmp_path / "book_samples").glob("date=*/hour=*/*.parquet"))
    assert parts, "expected parquet part files under date=/hour="
    assert store.samples_written == 3
