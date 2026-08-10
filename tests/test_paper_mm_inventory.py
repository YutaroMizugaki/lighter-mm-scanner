"""Inventory and quote strategy tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import on_book, on_trade
from lighter_mm.paper_mm.models import BookSnapshot, PaperMmConfig, PaperMmState, TradeEvent


def test_long_position_only_asks() -> None:
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

    on_trade(
        state,
        config,
        TradeEvent(10_001, 1, 100.0, 150.0, False),
        mid_hist,
    )
    assert state.position_qty_base > 0
    assert state.bid_order is None

    on_book(
        state,
        config,
        BookSnapshot(
            timestamp_ms=10_005,
            best_bid=100.0,
            best_ask=101.0,
            best_bid_size_usd=100.0,
            best_ask_size_usd=100.0,
            mid=100.5,
        ),
        mid_hist,
    )
    assert state.bid_order is None
    assert state.ask_order is not None
    assert state.ask_order.target_qty_base == pytest.approx(state.position_qty_base)
