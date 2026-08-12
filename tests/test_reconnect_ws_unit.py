"""Unit-level reconnect / shard planning tests (no live network)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from lighter_mm.config import Settings
from lighter_mm.models import MarketMeta, MarketStatus, MarketType
from lighter_mm.ws.manager import WsManager


def _meta(mid: int) -> MarketMeta:
    return MarketMeta(
        market_id=mid,
        symbol=f"M{mid}",
        market_type=MarketType.PERP,
        status=MarketStatus.ACTIVE,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        min_base_amount=Decimal("0.01"),
        min_quote_amount=Decimal("1"),
        supported_price_decimals=2,
        supported_size_decimals=4,
    )


def test_shard_plan_respects_subscription_cap() -> None:
    settings = Settings(max_subscriptions_per_connection=9)  # 1 stats + 4 markets*2
    markets = {i: _meta(i) for i in range(10)}
    mgr = WsManager(settings=settings, markets=markets)
    shards = mgr.plan_shards(markets.keys())
    assert len(shards) >= 2
    # Every shard carries market_stats/all so stats survive a single-shard drop.
    assert all(s.include_market_stats_all for s in shards)
    for s in shards:
        assert len(s.channels()) <= settings.max_subscriptions_per_connection


def test_stop_invalidates_books() -> None:
    settings = Settings()
    markets = {0: _meta(0)}
    mgr = WsManager(settings=settings, markets=markets)
    book = mgr.books[0]
    book.apply_snapshot(
        {
            "nonce": 10,
            "begin_nonce": 0,
            "bids": [{"price": "1", "size": "1"}],
            "asks": [{"price": "2", "size": "1"}],
        }
    )
    assert book.synced

    async def _run() -> None:
        await mgr.stop()

    asyncio.run(_run())
    assert book.synced is False
    assert not book.bids and not book.asks


def test_clean_disconnect_invalidates_shard_books() -> None:
    from lighter_mm.ws.manager import ShardPlan

    settings = Settings()
    markets = {0: _meta(0)}
    mgr = WsManager(settings=settings, markets=markets)
    book = mgr.books[0]
    book.apply_snapshot(
        {
            "nonce": 10,
            "begin_nonce": 0,
            "bids": [{"price": "1", "size": "1"}],
            "asks": [{"price": "2", "size": "1"}],
        }
    )
    assert book.synced
    shard = ShardPlan(shard_id=0, market_ids=[0])
    mgr._handle_shard_disconnect(shard, "clean close")
    assert book.synced is False
    assert not book.bids and not book.asks
    assert mgr.runtime.dropped_connections == 1
    assert mgr.runtime.book_resyncs >= 1
