"""Paper MM incremental markout tests."""

from __future__ import annotations

import pytest

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import finalize_state, on_book, on_trade
from lighter_mm.paper_mm.models import (
    BookSnapshot,
    PaperMmConfig,
    PaperMmState,
    PaperOrder,
    TradeEvent,
)


def test_markout_survives_long_replay_after_mid_prune() -> None:
    state = PaperMmState()
    config = PaperMmConfig()
    mid_hist = MidHistory(retention_seconds=60.0)

    on_book(
        state,
        config,
        BookSnapshot(10_000, 100.0, 101.0, 100.0, 100.0, 100.0),
        mid_hist,
    )

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
        BookSnapshot(16_000, 100.0, 101.0, 100.0, 100.0, 105.0),
        mid_hist,
    )
    on_book(
        state,
        config,
        BookSnapshot(41_000, 100.0, 101.0, 100.0, 100.0, 110.0),
        mid_hist,
    )

    for ts in range(60_000, 3_700_000, 60_000):
        on_book(
            state,
            config,
            BookSnapshot(ts, 100.0, 101.0, 100.0, 100.0, 100.0),
            mid_hist,
        )

    out = finalize_state(state, config, mid_hist, 3_700_000, 1.0)
    assert out["paper_mm_markout_5s_count"] == 1
    assert out["paper_mm_markout_30s_count"] == 1
    assert out["paper_mm_markout_5s_median_bps"] is not None
    assert out["paper_mm_markout_30s_median_bps"] is not None


def test_markout_multiple_fills_across_window() -> None:
    state = PaperMmState()
    config = PaperMmConfig()
    mid_hist = MidHistory(retention_seconds=120.0)

    def book(ts: int, mid: float) -> None:
        on_book(
            state,
            config,
            BookSnapshot(ts, 100.0, 101.0, 100.0, 100.0, mid),
            mid_hist,
        )

    book(0, 100.0)
    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=0,
        last_seen_at_ms=0,
    )
    on_trade(state, config, TradeEvent(1, 1, 100.0, 100.0, False), mid_hist)
    book(6_000, 105.0)
    book(31_000, 110.0)

    state.bid_order = PaperOrder(
        side="bid",
        price=100.0,
        target_qty_base=1.0,
        remaining_qty_base=1.0,
        queue_ahead_usd=0.0,
        placed_at_ms=60_000,
        last_seen_at_ms=60_000,
    )
    on_trade(state, config, TradeEvent(60_001, 2, 100.0, 100.0, False), mid_hist)
    book(66_000, 108.0)
    book(91_000, 112.0)

    out = finalize_state(state, config, mid_hist, 120_000, 1.0)
    assert out["paper_mm_markout_5s_count"] == 2
    assert out["paper_mm_markout_30s_count"] == 2
