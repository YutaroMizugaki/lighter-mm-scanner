"""Regression tests for the post-PR9 correctness audit."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import _trade_stats
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.engine.trade_activity import TradeActivityTracker
from lighter_mm.models import TradeEvent, TradeType
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.scoring import CandidateThresholds, score_markets
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.state import RunState
from lighter_mm.ws.manager import WsManager
from tests.helpers import enrich_book_row


def _book_row(ts: int, market_id: int = 1) -> dict:
    return enrich_book_row({
        "timestamp_ms": ts,
        "market_id": market_id,
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
    })


def test_unsynced_book_drops_delta_instead_of_snapshot_apply() -> None:
    """Nonce-gap / disconnect must not treat the next delta as a full snapshot."""
    settings = Settings()
    mgr = WsManager(settings=settings, markets={})
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 100,
            "begin_nonce": 0,
            "bids": [
                {"price": "1999.0", "size": "2.0"},
                {"price": "1998.0", "size": "3.0"},
            ],
            "asks": [
                {"price": "2000.0", "size": "1.5"},
                {"price": "2001.0", "size": "4.0"},
            ],
        }
    )
    assert len(book.bids) == 2 and len(book.asks) == 2
    book.mark_resync()
    mgr.books[0] = book

    async def _run() -> None:
        await mgr._handle_order_book(
            {
                "channel": "order_book:0",
                "timestamp": 1,
                "order_book": {
                    "begin_nonce": 100,
                    "nonce": 110,
                    "bids": [{"price": "1999.0", "size": "1.0"}],
                    "asks": [{"price": "2000.0", "size": "1.0"}],
                },
            },
            is_snapshot=False,
        )

    asyncio.run(_run())
    assert book.synced is False
    assert len(book.bids) == 0 and len(book.asks) == 0

    async def _snap() -> None:
        await mgr._handle_order_book(
            {
                "channel": "order_book:0",
                "timestamp": 2,
                "order_book": {
                    "nonce": 200,
                    "begin_nonce": 0,
                    "bids": [{"price": "1999.0", "size": "2.0"}],
                    "asks": [{"price": "2000.0", "size": "1.5"}],
                },
            },
            is_snapshot=True,
        )

    asyncio.run(_snap())
    assert book.synced is True
    assert len(book.bids) == 1 and len(book.asks) == 1


def test_parquet_upload_skips_open_writer_files(tmp_path: Path) -> None:
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10], flush_rows=1, flush_seconds=60, rotation_minutes=15
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))
    store.rotate_all()
    closed = store.take_closed_paths()
    assert closed, "rotated part should be closed and uploadable"

    # New writes open a fresh part that must not be in the closed set yet.
    store.write_book(_book_row(ts + 1000))
    assert store.book.open_path() is not None
    assert store.book.open_path() not in closed
    assert store.take_closed_paths() == []

    # Failed upload requeues only the closed paths.
    store.requeue_closed_paths(closed)
    again = store.take_closed_paths()
    assert again == closed
    store.close()


def test_trade_stats_pads_zero_minutes() -> None:
    # One trade in a 10-minute window → mean TPM = 0.1, median = 0
    df = pl.DataFrame(
        {
            "market_id": [1],
            "type": ["trade"],
            "timestamp_ms": [1_700_000_000_000],
            "usd_amount": [10.0],
        }
    )
    stats = _trade_stats(df, 1, window_minutes=10)
    assert stats["total_trade_count"] == 1
    assert abs(stats["trades_per_minute_mean"] - 0.1) < 1e-9
    assert stats["trades_per_minute_median"] == 0.0


def test_missing_markouts_block_candidacy() -> None:
    row = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 1.0,
        "data_coverage_pct": 95.0,
        "trades_per_minute_median": 2.0,
        "total_trade_count": 120,
        "median_two_sided_depth_5bps_usd": 500.0,
        "median_two_sided_depth_10bps_usd": 800.0,
        "median_spread_bps": 3.0,
        "maker_markout_5s_median_bps": None,
        "maker_markout_30s_median_bps": None,
        "pct_time_spread_ge_5bps": 0.5,
    }
    scored = score_markets([row], thresholds=CandidateThresholds())
    assert scored[0].candidate is False


def test_remaining_observation_uses_started_at() -> None:
    # Bypass full collector init network/storage by constructing minimally.
    app = object.__new__(CollectorApp)
    app.hours = 72.0
    app.state = RunState(
        run_id="abc",
        started_at=(datetime.now(UTC) - timedelta(hours=80)).isoformat(),
        status="running",
        observation_target_hours=72.0,
    )
    remaining = CollectorApp._remaining_observation_seconds(app)
    assert remaining is not None
    assert remaining < 0


def test_trade_activity_pop_closed_minutes() -> None:
    tracker = TradeActivityTracker()
    now = 1_700_000_120_000
    trade = TradeEvent(
        trade_id=1,
        timestamp_ms=now - 120_000,
        market_id=7,
        price=__import__("decimal").Decimal("1"),
        size=__import__("decimal").Decimal("1"),
        usd_amount=__import__("decimal").Decimal("1"),
        is_maker_ask=True,
        type=TradeType.TRADE,
    )
    tracker.on_trade(trade)
    assert 7 in tracker._buckets and tracker._buckets[7]
    closed = tracker.pop_closed_minutes(now)
    assert len(closed) == 1
    assert tracker._buckets[7] == {}


def test_closed_parquet_is_readable(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=1, flush_seconds=60)
    store.write_book(_book_row(int(time.time() * 1000)))
    store.rotate_all()
    paths = store.take_closed_paths()
    assert paths
    table = pq.read_table(paths[0])
    assert table.num_rows == 1
    store.close()
