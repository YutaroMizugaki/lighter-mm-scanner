"""Paper MM PnL accounting tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import (
    _compute_unrealized_pnl,
    finalize_state,
    on_book,
    on_trade,
)
from lighter_mm.paper_mm.models import (
    BookSnapshot,
    FifoLot,
    PaperMmConfig,
    PaperMmState,
    PaperOrder,
    TradeEvent,
)


def _finalize(state: PaperMmState, mid_hist: MidHistory, end_ms: int) -> dict:
    return finalize_state(state, PaperMmConfig(), mid_hist, end_ms, 1.0)


def test_pnl_flat_after_round_trip() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()
    config = PaperMmConfig()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 100.0, 100.0, False), mid_hist)

    state.ask_order = PaperOrder(
        side="ask",
        price=101.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_010,
        last_seen_at_ms=10_010,
    )
    on_trade(state, config, TradeEvent(10_011, 2, 101.0, 101.0, True), mid_hist)

    on_book(
        state,
        config,
        BookSnapshot(10_020, 100.0, 101.0, 100.0, 100.0, 100.5),
        mid_hist,
    )

    out = _finalize(state, mid_hist, 20_000)
    assert out["paper_mm_realized_pnl_usd"] == pytest.approx(1.0)
    assert out["paper_mm_unrealized_pnl_usd"] == pytest.approx(0.0)
    assert out["paper_mm_gross_pnl_usd"] == pytest.approx(1.0)


def test_pnl_no_double_count_open_inventory() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()
    config = PaperMmConfig()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 100.0, 100.0, False), mid_hist)

    state.ask_order = PaperOrder(
        side="ask",
        price=101.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_010,
        last_seen_at_ms=10_010,
    )
    on_trade(state, config, TradeEvent(10_011, 2, 101.0, 101.0, True), mid_hist)

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_020,
        last_seen_at_ms=10_020,
    )
    on_trade(state, config, TradeEvent(10_021, 3, 100.0, 100.0, False), mid_hist)

    on_book(
        state,
        config,
        BookSnapshot(10_030, 100.0, 101.0, 100.0, 100.0, 102.0),
        mid_hist,
    )

    out = _finalize(state, mid_hist, 20_000)
    assert out["paper_mm_realized_pnl_usd"] == pytest.approx(1.0)
    assert out["paper_mm_unrealized_pnl_usd"] == pytest.approx(2.0)
    assert out["paper_mm_gross_pnl_usd"] == pytest.approx(3.0)
    assert out["paper_mm_gross_pnl_usd"] != pytest.approx(4.0)


def test_pnl_short_unrealized() -> None:
    state = PaperMmState()
    state.fifo_lots.append(FifoLot(1.0, 100.0, "short"))
    state.position_qty_base = -1.0
    state.cash_usd = 100.0

    unrealized = _compute_unrealized_pnl(state, 98.0)
    assert unrealized == pytest.approx(2.0)

    state.realized_pnl_usd = 0.0
    state.last_mid = 98.0
    mid_hist = MidHistory()
    out = _finalize(state, mid_hist, 20_000)
    assert out["paper_mm_unrealized_pnl_usd"] == pytest.approx(2.0)
    assert out["paper_mm_gross_pnl_usd"] == pytest.approx(2.0)


def test_gross_pnl_matches_cash_plus_position() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()
    config = PaperMmConfig()

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=10_000,
        last_seen_at_ms=10_000,
    )
    on_trade(state, config, TradeEvent(10_001, 1, 100.0, 100.0, False), mid_hist)
    on_book(
        state,
        config,
        BookSnapshot(10_020, 100.0, 101.0, 100.0, 100.0, 102.0),
        mid_hist,
    )

    out = _finalize(state, mid_hist, 20_000)
    last_mid = 102.0
    accounting = state.cash_usd + state.position_qty_base * last_mid
    assert out["paper_mm_gross_pnl_usd"] == pytest.approx(accounting)
