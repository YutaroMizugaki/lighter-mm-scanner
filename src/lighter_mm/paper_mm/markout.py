"""Paper fill markout — incremental resolution on book events."""

from __future__ import annotations

from lighter_mm.engine.markout import FORWARD_WAIT_MS, MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.models import PaperFill, PaperMmState

MARKOUT_HORIZONS_MS = (5000, 30000)


def resolve_due_paper_markouts(
    state: PaperMmState,
    mid_hist: MidHistory,
    now_ms: int,
    *,
    end_ms: int | None = None,
) -> None:
    """Resolve paper fill markouts whose horizons have passed at ``now_ms``."""
    cap_ms = end_ms if end_ms is not None else now_ms
    for fill in state.paper_fills:
        _resolve_fill_horizons(fill, state, mid_hist, now_ms=now_ms, cap_ms=cap_ms)


def drain_pending_paper_markouts(
    state: PaperMmState,
    mid_hist: MidHistory,
    end_ms: int,
) -> None:
    """Final drain at analysis window end; does not look beyond ``end_ms``."""
    resolve_due_paper_markouts(state, mid_hist, now_ms=end_ms, end_ms=end_ms)


def _resolve_fill_horizons(
    fill: PaperFill,
    state: PaperMmState,
    mid_hist: MidHistory,
    *,
    now_ms: int,
    cap_ms: int,
) -> None:
    is_maker_ask = fill.side == "ask"
    ref = fill.reference_mid

    if not fill.markout_5s_resolved:
        target_5s = fill.timestamp_ms + MARKOUT_HORIZONS_MS[0]
        if now_ms >= target_5s and target_5s <= cap_ms:
            future = _resolve_future_mid(mid_hist, target_5s, now_ms)
            if future is not None:
                bps = MarkoutEngine.compute_markout_bps(
                    trade_price=fill.price,
                    future_mid=future,
                    reference_mid=ref,
                    is_maker_ask=is_maker_ask,
                )
                if bps is not None:
                    state.markout_5s_bps.append(bps)
                    fill.markout_5s_resolved = True

    if not fill.markout_30s_resolved:
        target_30s = fill.timestamp_ms + MARKOUT_HORIZONS_MS[1]
        if now_ms >= target_30s and target_30s <= cap_ms:
            future = _resolve_future_mid(mid_hist, target_30s, now_ms)
            if future is not None:
                bps = MarkoutEngine.compute_markout_bps(
                    trade_price=fill.price,
                    future_mid=future,
                    reference_mid=ref,
                    is_maker_ask=is_maker_ask,
                )
                if bps is not None:
                    state.markout_30s_bps.append(bps)
                    fill.markout_30s_resolved = True


def _resolve_future_mid(
    hist: MidHistory,
    target_ms: int,
    now_ms: int,
) -> float | None:
    tolerance_ms = FORWARD_WAIT_MS
    if now_ms <= target_ms + FORWARD_WAIT_MS:
        return hist.mid_at_or_after(target_ms, tolerance_ms=tolerance_ms)
    forward = hist.mid_at_or_after(target_ms, tolerance_ms=tolerance_ms)
    if forward is not None:
        return forward
    return hist.mid_at_or_before(target_ms, tolerance_ms=tolerance_ms)
