"""Paper MM fill price and max inventory tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import on_book, on_trade
from lighter_mm.paper_mm.models import (
    BookSnapshot,
    PaperMmConfig,
    PaperMmState,
    PaperOrder,
    TradeEvent,
)


def test_bid_through_trade_fills_at_order_price() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=0.5,
        remaining_qty_base=0.5,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 99.0, 50.0, False), mid_hist)

    assert state.paper_fills[0].price == 100.0
    assert state.filled_notional_usd == pytest.approx(50.0)


def test_ask_through_trade_fills_at_order_price() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    state.ask_order = PaperOrder(
        side="ask",
        price=101.0,
        target_qty_base=0.5,
        remaining_qty_base=0.5,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 102.0, 60.0, True), mid_hist)

    assert state.paper_fills[0].price == 101.0
    assert state.filled_notional_usd == pytest.approx(50.5)


def test_touch_trade_fills_at_order_price() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0)
    mid_hist = MidHistory()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=0.5,
        remaining_qty_base=0.5,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 100.0, 50.0, False), mid_hist)
    assert state.paper_fills[0].price == 100.0


def test_max_inventory_caps_entry_size() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=100.0, max_inventory_usd=50.0)
    mid_hist = MidHistory()

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_000,
            best_bid=100.0,
            best_ask=101.0,
            best_bid_size_usd=200.0,
            best_ask_size_usd=200.0,
            mid=100.5,
        ),
        mid_hist,
    )

    assert state.bid_order is not None
    assert state.bid_order.target_qty_base == pytest.approx(0.5)
    assert state.ask_order is not None
    assert state.ask_order.target_qty_base == pytest.approx(50.0 / 101.0)


def test_entry_size_when_order_below_max_inventory() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=25.0, max_inventory_usd=50.0)
    mid_hist = MidHistory()

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_000,
            best_bid=100.0,
            best_ask=101.0,
            best_bid_size_usd=200.0,
            best_ask_size_usd=200.0,
            mid=100.5,
        ),
        mid_hist,
    )

    assert state.bid_order is not None
    assert state.bid_order.target_qty_base == pytest.approx(0.25)
