"""Engine requote and look-ahead tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import on_book, on_trade
from lighter_mm.paper_mm.models import BookSnapshot, PaperMmConfig, PaperMmState, PaperOrder, TradeEvent


def test_requote_on_best_bid_change() -> None:
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
    assert state.bid_order.price == 100.0
    assert state.bid_order.queue_ahead_usd == 100.0

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_005,
            best_bid=101.0,
            best_ask=102.0,
            best_bid_size_usd=200.0,
            best_ask_size_usd=200.0,
            mid=101.5,
        ),
        mid_hist,
    )
    assert state.bid_order is not None
    assert state.bid_order.price == 101.0
    assert state.bid_order.queue_ahead_usd == 200.0


def test_lookahead_same_timestamp_trade_no_fill() -> None:
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
            best_bid_size_usd=0.0,
            best_ask_size_usd=100.0,
            mid=100.5,
        ),
        mid_hist,
    )
    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=0.5,
        remaining_qty_base=0.5,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )

    on_trade(state, config, TradeEvent(10_000, 1, 100.0, 50.0, False), mid_hist)
    assert state.filled_notional_usd == 0.0

    on_trade(state, config, TradeEvent(10_001, 2, 100.0, 50.0, False), mid_hist)
    assert state.filled_notional_usd == pytest.approx(50.0)


def test_trade_does_not_overfill_remaining_order() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=0.5,
        remaining_qty_base=0.3,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )

    on_trade(
        state,
        config,
        TradeEvent(10_005, 4, 100.0, 50.0, False),
        mid_hist,
    )

    assert state.filled_notional_usd == pytest.approx(30.0)
