"""PnL and fee tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import on_trade
from lighter_mm.paper_mm.models import PaperMmConfig, PaperMmState, PaperOrder, TradeEvent


def test_round_trip_positive_pnl() -> None:
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
    assert state.position_qty_base == pytest.approx(0.5)

    state.ask_order = PaperOrder(
        side="ask",
        price=101.0,
        target_qty_base=0.5,
        remaining_qty_base=0.5,
        queue_ahead_usd=0.0,
        placed_at_ms=10_010,
        last_seen_at_ms=10_010,
    )
    on_trade(state, config, TradeEvent(10_011, 2, 101.0, 60.0, True), mid_hist)

    assert state.position_qty_base == pytest.approx(0.0)
    assert state.realized_pnl_usd == pytest.approx(0.5)
    assert state.round_trips == 1


def test_fee_reduces_pnl() -> None:
    state = PaperMmState()
    config = PaperMmConfig(order_usd=50.0, maker_fee_bps=10.0)
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
    assert state.fees_usd == pytest.approx(0.05)
