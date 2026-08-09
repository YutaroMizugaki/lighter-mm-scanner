"""WebSocket shard cap + trade payload normalization."""

from __future__ import annotations

from decimal import Decimal

from lighter_mm.config import Settings
from lighter_mm.models import MarketMeta, MarketStatus, MarketType
from lighter_mm.ws.manager import WsManager, WsRuntimeStats, _normalize_ws_items


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


def test_205_markets_respect_subscription_cap_95() -> None:
    settings = Settings(max_subscriptions_per_connection=95)
    markets = {i: _meta(i) for i in range(205)}
    mgr = WsManager(settings=settings, markets=markets)
    shards = mgr.plan_shards(markets.keys())

    # ~47 markets/shard → ~5 shards for 205 markets
    assert 4 <= len(shards) <= 6
    assigned: list[int] = []
    for shard in shards:
        assert len(shard.channels()) <= 95
        assert shard.include_market_stats_all
        assigned.extend(shard.market_ids)
    assert sorted(assigned) == list(range(205))
    assert len(assigned) == 205


def test_default_settings_cap_is_95() -> None:
    assert Settings().max_subscriptions_per_connection == 95


def test_normalize_ws_items_list() -> None:
    msg = {"trades": [{"trade_id": 1}, {"trade_id": 2}]}
    out = _normalize_ws_items(msg.get("trades"))
    assert len(out) == 2
    assert out[0]["trade_id"] == 1


def test_normalize_ws_items_object() -> None:
    msg = {"trades": {"trade_id": 9}}
    out = _normalize_ws_items(msg.get("trades"))
    assert len(out) == 1
    assert out[0]["trade_id"] == 9


def test_normalize_ws_items_null() -> None:
    msg = {"trades": None}
    assert _normalize_ws_items(msg.get("trades")) == []


def test_normalize_ws_items_malformed() -> None:
    msg = {"trades": "invalid"}
    assert _normalize_ws_items(msg.get("trades")) == []


def test_ws_runtime_public_dict_has_health_fields() -> None:
    stats = WsRuntimeStats(
        connected_shards=5,
        total_shards=5,
        planned_channels=415,
        acked_channels=415,
        subscribed_channels=415,
        dropped_connections=0,
        subscription_errors=2,
        trade_parse_errors=3,
        last_ws_error="too many subscriptions",
    )
    pub = stats.public_dict()
    assert pub["connected_shards"] == 5
    assert pub["total_shards"] == 5
    assert pub["planned_channels"] == 415
    assert pub["acked_channels"] == 415
    assert pub["subscribed_channels"] == 415
    assert pub["subscription_errors"] == 2
    assert pub["trade_parse_errors"] == 3
    assert "trade_id" not in pub
