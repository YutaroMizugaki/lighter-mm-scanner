"""Unit-level reconnect / shard planning tests (no live network)."""

from __future__ import annotations

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
    assert shards[0].include_market_stats_all is True
    assert sum(1 for s in shards if s.include_market_stats_all) == 1
    for s in shards:
        assert len(s.channels()) <= settings.max_subscriptions_per_connection
