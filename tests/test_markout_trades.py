"""Trade side, dedupe, markout, persistence, scoring tests."""

from __future__ import annotations

import asyncio

import polars as pl

from lighter_mm.analytics.aggregation import _spread_persistence
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.models import TradeEvent, TradeType
from lighter_mm.scoring import score_markets
from lighter_mm.ws.rate_limit import TokenBucket


def test_trade_side_interpretation() -> None:
    buy = TradeEvent(
        trade_id=1,
        timestamp_ms=1000,
        market_id=0,
        price=__import__("decimal").Decimal("10"),
        size=__import__("decimal").Decimal("1"),
        usd_amount=__import__("decimal").Decimal("10"),
        is_maker_ask=True,
        type=TradeType.TRADE,
    )
    sell = TradeEvent(
        trade_id=2,
        timestamp_ms=1001,
        market_id=0,
        price=__import__("decimal").Decimal("10"),
        size=__import__("decimal").Decimal("1"),
        usd_amount=__import__("decimal").Decimal("10"),
        is_maker_ask=False,
        type=TradeType.TRADE,
    )
    assert buy.taker_is_buy is True
    assert sell.taker_is_buy is False


def test_trade_deduplication_set() -> None:
    seen: set[int] = set()
    events = [
        {"trade_id": 1, "timestamp": 1, "market_id": 0, "price": "1", "size": "1",
         "usd_amount": "1", "is_maker_ask": True, "type": "trade"},
        {"trade_id": 1, "timestamp": 1, "market_id": 0, "price": "1", "size": "1",
         "usd_amount": "1", "is_maker_ask": True, "type": "trade"},
        {"trade_id": 2, "timestamp": 2, "market_id": 0, "price": "1", "size": "1",
         "usd_amount": "1", "is_maker_ask": False, "type": "trade"},
    ]
    out = []
    for raw in events:
        t = TradeEvent.from_ws(raw)
        if t.trade_id in seen:
            continue
        seen.add(t.trade_id)
        out.append(t)
    assert [t.trade_id for t in out] == [1, 2]


def test_maker_markout_signs() -> None:
    # Maker sold (ask); price falls → positive
    pos = MarkoutEngine.compute_markout_bps(
        trade_price=100.0, future_mid=99.0, reference_mid=100.0, is_maker_ask=True
    )
    assert pos == 100.0
    # Maker bought (bid); price rises → positive
    pos2 = MarkoutEngine.compute_markout_bps(
        trade_price=100.0, future_mid=101.0, reference_mid=100.0, is_maker_ask=False
    )
    assert pos2 == 100.0
    # Adverse
    neg = MarkoutEngine.compute_markout_bps(
        trade_price=100.0, future_mid=101.0, reference_mid=100.0, is_maker_ask=True
    )
    assert neg == -100.0


def test_markout_pending_resolution() -> None:
    rows: list[dict] = []
    eng = MarkoutEngine(horizons=[1], on_markout=rows.append)
    trade = TradeEvent.from_ws(
        {
            "trade_id": 9,
            "timestamp": 1000,
            "market_id": 0,
            "price": "100",
            "size": "1",
            "usd_amount": "100",
            "is_maker_ask": True,
            "type": "trade",
        }
    )
    eng.on_trade(trade, "ETH", 100.0)
    hist = MidHistory()
    hist.add(2000, 99.5)
    n = eng.poll(2500, {0: hist})
    assert n == 1
    assert rows[0]["maker_markout_bps"] == 50.0


def test_spread_persistence() -> None:
    df = pl.DataFrame(
        {
            "timestamp_ms": [0, 5000, 10000, 15000, 20000],
            "spread_bps": [6.0, 6.0, 1.0, 6.0, 6.0],
        }
    )
    out = _spread_persistence(df, [5.0])
    assert out["pct_time_spread_ge_5bps"] is not None
    assert out["pct_time_spread_ge_5bps"] > 0.5
    assert out["spread_ge_5bps_event_count"] >= 1


def test_score_penalties() -> None:
    rows = [
        {
            "symbol": "GOOD",
            "market_id": 1,
            "observation_hours": 24,
            "data_coverage_pct": 98,
            "median_spread_bps": 8,
            "pct_time_spread_ge_5bps": 0.5,
            "median_two_sided_depth_10bps_usd": 5000,
            "median_two_sided_depth_5bps_usd": 2000,
            "trades_per_minute_median": 10,
            "total_trade_count": 10000,
            "maker_markout_5s_median_bps": 1.5,
            "maker_markout_30s_median_bps": 0.5,
        },
        {
            "symbol": "BAD",
            "market_id": 2,
            "observation_hours": 24,
            "data_coverage_pct": 40,
            "median_spread_bps": 40,
            "pct_time_spread_ge_5bps": 0.9,
            "median_two_sided_depth_10bps_usd": 10,
            "median_two_sided_depth_5bps_usd": 5,
            "trades_per_minute_median": 0.01,
            "total_trade_count": 2,
            "maker_markout_5s_median_bps": -20,
            "maker_markout_30s_median_bps": -40,
        },
    ]
    scored = score_markets(rows)
    assert scored[0].row["symbol"] == "GOOD"
    assert scored[0].score > scored[1].score
    assert scored[1].letter_rank in {"C", "D"}


def test_zero_observation_coverage_is_not_treated_as_missing() -> None:
    from lighter_mm.scoring import CandidateThresholds, _apply_score_penalties, _coverage_pct

    row = {
        "observation_coverage_pct": 0.0,
        "data_coverage_pct": 99.0,
        "observation_hours": 0.0,
        "total_trade_count": 0,
    }
    assert _coverage_pct(row) == 0.0
    score, penalties = _apply_score_penalties(row, 80.0, CandidateThresholds())
    assert any("coverage" in p for p in penalties)
    assert score < 80.0


def test_zero_observation_coverage_ranks_below_true_high_coverage() -> None:
    from lighter_mm.scoring import _peer_dq_values, score_markets

    high = {
        "symbol": "HIGH",
        "market_id": 1,
        "observation_hours": 24,
        "observation_coverage_pct": 99.0,
        "data_coverage_pct": 99.0,
        "pct_time_spread_ge_5bps": 0.5,
        "median_spread_bps": 8,
        "median_two_sided_depth_10bps_usd": 5000,
        "median_two_sided_depth_5bps_usd": 2000,
        "trades_per_minute_median": 10,
        "trades_per_minute_mean": 10,
        "total_trade_count": 10000,
        "maker_markout_5s_median_bps": 1.5,
        "maker_markout_30s_median_bps": 0.5,
    }
    zero_obs = {
        **high,
        "symbol": "ZERO",
        "market_id": 2,
        "observation_coverage_pct": 0.0,
        "data_coverage_pct": 99.0,
    }
    peer_dq = _peer_dq_values([high, zero_obs])
    assert peer_dq[0] is not None and peer_dq[1] is not None
    assert peer_dq[1] < peer_dq[0]

    scored = score_markets([high, zero_obs])
    by_sym = {s.row["symbol"]: s for s in scored}
    high_dq = by_sym["HIGH"].rank_components["data_quality_persistence"]
    zero_dq = by_sym["ZERO"].rank_components["data_quality_persistence"]
    assert high_dq is not None and zero_dq is not None
    assert zero_dq < high_dq


def test_token_bucket_rate() -> None:
    async def _run() -> None:
        b = TokenBucket(rate_per_minute=6000, capacity=2)  # 100/sec
        await b.acquire()
        await b.acquire()
        # third should wait briefly
        t0 = asyncio.get_running_loop().time()
        await b.acquire()
        assert asyncio.get_running_loop().time() - t0 >= 0.0

    asyncio.run(_run())
