"""Paper MM incremental markout tests."""

from __future__ import annotations

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import finalize_state, on_book, on_trade
from lighter_mm.paper_mm.markout import (
    MARKOUT_HORIZON_30S_MS,
    drain_pending_paper_markouts,
    register_pending_markout,
    resolve_due_paper_markouts,
)
from lighter_mm.paper_mm.models import (
    BookSnapshot,
    PaperFill,
    PaperMmConfig,
    PaperMmState,
    PaperOrder,
    TradeEvent,
)


def test_resolved_fill_removes_pending() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()

    fill = PaperFill(
        side="bid",
        qty_base=1.0,
        price=100.0,
        usd=100.0,
        timestamp_ms=0,
        reference_mid=100.0,
        is_partial=False,
    )
    state.paper_fills.append(fill)
    register_pending_markout(state, fill)

    mid_hist.add(6_000, 105.0)
    resolve_due_paper_markouts(state, mid_hist, 6_000)
    assert len(state.pending_markouts) == 1

    mid_hist.add(31_000, 110.0)
    resolve_due_paper_markouts(state, mid_hist, 31_000)
    assert len(state.pending_markouts) == 0
    assert fill.markout_5s_resolved
    assert fill.markout_30s_resolved


def test_missing_mid_horizon_expires() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()

    fill = PaperFill(
        side="bid",
        qty_base=1.0,
        price=100.0,
        usd=100.0,
        timestamp_ms=0,
        reference_mid=100.0,
        is_partial=False,
    )
    state.paper_fills.append(fill)
    register_pending_markout(state, fill)

    # No mid near 5s target; expire after target + 10s
    resolve_due_paper_markouts(state, mid_hist, 20_000)
    assert len(state.markout_5s_bps) == 0
    assert len(state.pending_markouts) == 1
    assert state.pending_markouts[0].remaining_horizons_ms == {MARKOUT_HORIZON_30S_MS}

    resolve_due_paper_markouts(state, mid_hist, 50_000)
    assert len(state.markout_30s_bps) == 0
    assert len(state.pending_markouts) == 0


def test_many_fills_pending_queue_stays_bounded() -> None:
    state = PaperMmState()
    config = PaperMmConfig()
    mid_hist = MidHistory(retention_seconds=120.0)

    for i in range(120):
        ts = i * 60_000
        state.bid_order = PaperOrder(
            side="bid",
            price=100.0,
            target_qty_base=1.0,
            remaining_qty_base=1.0,
            queue_ahead_usd=0.0,
            placed_at_ms=ts,
            last_seen_at_ms=ts,
        )
        on_trade(state, config, TradeEvent(ts + 1, i, 100.0, 100.0, False), mid_hist)
        on_book(
            state,
            config,
            BookSnapshot(ts + 6_000, 100.0, 101.0, 100.0, 100.0, 105.0),
            mid_hist,
        )
        on_book(
            state,
            config,
            BookSnapshot(ts + 31_000, 100.0, 101.0, 100.0, 100.0, 110.0),
            mid_hist,
        )

    assert len(state.paper_fills) == 120
    assert len(state.pending_markouts) == 0
    assert state.markout_5s_bps
    assert state.markout_30s_bps


def test_finalize_drain_does_not_resolve_beyond_end_ms() -> None:
    state = PaperMmState()
    mid_hist = MidHistory()

    fill = PaperFill(
        side="bid",
        qty_base=1.0,
        price=100.0,
        usd=100.0,
        timestamp_ms=97_000,
        reference_mid=100.0,
        is_partial=False,
    )
    state.paper_fills.append(fill)
    register_pending_markout(state, fill)
    mid_hist.add(100_000, 100.0)

    drain_pending_paper_markouts(state, mid_hist, end_ms=100_000)
    assert len(state.markout_5s_bps) == 0
    assert len(state.pending_markouts) == 0


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
