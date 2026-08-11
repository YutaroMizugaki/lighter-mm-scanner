"""Queue ahead consumption tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import on_book, on_trade
from lighter_mm.paper_mm.models import BookSnapshot, PaperMmConfig, PaperMmState, TradeEvent


def test_queue_consumption_before_fill() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_000,
            best_bid=100.0,
            best_ask=101.0,
            best_bid_size_usd=100.0,
            best_ask_size_usd=100.0,
            mid=100.5,
        ),
        mid_hist,
    )
    assert state.bid_order is not None
    assert state.bid_order.queue_ahead_usd == 100.0

    trades = [
        TradeEvent(10_001, 1, 100.0, 40.0, False),
        TradeEvent(10_002, 2, 100.0, 40.0, False),
    ]
    for t in trades:
        on_trade(state, config, t, mid_hist)

    assert state.bid_order is not None
    assert state.bid_order.queue_ahead_usd == 20.0
    assert state.filled_notional_usd == 0.0


def test_queue_partial_fill_on_third_trade() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_000,
            best_bid=100.0,
            best_ask=101.0,
            best_bid_size_usd=100.0,
            best_ask_size_usd=100.0,
            mid=100.5,
        ),
        mid_hist,
    )

    for tid, ts, usd in [(1, 10_001, 40.0), (2, 10_002, 40.0), (3, 10_003, 40.0)]:
        on_trade(
            state,
            config,
            TradeEvent(ts, tid, 100.0, usd, False),
            mid_hist,
        )

    assert state.filled_notional_usd == pytest.approx(20.0)
    assert state.position_qty_base == pytest.approx(0.2)
    assert state.bid_order is None
