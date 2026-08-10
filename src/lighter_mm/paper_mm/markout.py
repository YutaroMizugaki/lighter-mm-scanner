"""Paper fill markout using existing maker-positive semantics."""

from __future__ import annotations

from lighter_mm.engine.markout import FORWARD_WAIT_MS, MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.models import PaperMmState


def resolve_paper_markouts(
    state: PaperMmState,
    mid_hist: MidHistory,
    end_ms: int,
) -> None:
    for fill in state.paper_fills:
        target_5s = fill.timestamp_ms + 5000
        target_30s = fill.timestamp_ms + 30000
        if target_5s > end_ms and target_30s > end_ms:
            continue

        is_maker_ask = fill.side == "ask"
        ref = fill.reference_mid

        if target_5s <= end_ms:
            future = _resolve_future_mid(mid_hist, target_5s, end_ms)
            if future is not None:
                bps = MarkoutEngine.compute_markout_bps(
                    trade_price=fill.price,
                    future_mid=future,
                    reference_mid=ref,
                    is_maker_ask=is_maker_ask,
                )
                if bps is not None:
                    state.markout_5s_bps.append(bps)

        if target_30s <= end_ms:
            future = _resolve_future_mid(mid_hist, target_30s, end_ms)
            if future is not None:
                bps = MarkoutEngine.compute_markout_bps(
                    trade_price=fill.price,
                    future_mid=future,
                    reference_mid=ref,
                    is_maker_ask=is_maker_ask,
                )
                if bps is not None:
                    state.markout_30s_bps.append(bps)


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
