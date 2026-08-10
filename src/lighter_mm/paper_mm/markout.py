"""Paper fill markout — pending queue with incremental resolution."""

from __future__ import annotations

from collections import deque

from lighter_mm.engine.markout import FORWARD_WAIT_MS, MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.models import PaperFill, PaperMmState, PendingPaperMarkout

MARKOUT_HORIZON_5S_MS = 5000
MARKOUT_HORIZON_30S_MS = 30000
MARKOUT_HORIZONS_MS = (MARKOUT_HORIZON_5S_MS, MARKOUT_HORIZON_30S_MS)
MARKOUT_EXPIRE_AFTER_MS = 10_000


def register_pending_markout(state: PaperMmState, fill: PaperFill) -> None:
    state.pending_markouts.append(
        PendingPaperMarkout(
            fill=fill,
            remaining_horizons_ms={MARKOUT_HORIZON_5S_MS, MARKOUT_HORIZON_30S_MS},
        )
    )


def resolve_due_paper_markouts(
    state: PaperMmState,
    mid_hist: MidHistory,
    now_ms: int,
    *,
    end_ms: int | None = None,
) -> None:
    """Resolve or expire pending paper markouts whose horizons are due at ``now_ms``."""
    keep: deque[PendingPaperMarkout] = deque()

    while state.pending_markouts:
        pending = state.pending_markouts.popleft()
        _resolve_pending_horizons(
            pending,
            state,
            mid_hist,
            now_ms=now_ms,
            end_ms=end_ms,
        )
        if pending.remaining_horizons_ms:
            keep.append(pending)

    state.pending_markouts = keep


def drain_pending_paper_markouts(
    state: PaperMmState,
    mid_hist: MidHistory,
    end_ms: int,
) -> None:
    """Final drain at analysis window end; does not look beyond ``end_ms``."""
    resolve_due_paper_markouts(state, mid_hist, now_ms=end_ms, end_ms=end_ms)


def _resolve_pending_horizons(
    pending: PendingPaperMarkout,
    state: PaperMmState,
    mid_hist: MidHistory,
    *,
    now_ms: int,
    end_ms: int | None,
) -> None:
    fill = pending.fill
    is_maker_ask = fill.side == "ask"
    ref = fill.reference_mid

    for horizon_ms in list(pending.remaining_horizons_ms):
        target_ms = fill.timestamp_ms + horizon_ms
        if end_ms is not None and target_ms > end_ms:
            pending.remaining_horizons_ms.discard(horizon_ms)
            continue
        if now_ms < target_ms:
            continue

        future = _resolve_future_mid(mid_hist, target_ms, now_ms)
        if future is not None:
            bps = MarkoutEngine.compute_markout_bps(
                trade_price=fill.price,
                future_mid=future,
                reference_mid=ref,
                is_maker_ask=is_maker_ask,
            )
            if bps is not None:
                if horizon_ms == MARKOUT_HORIZON_5S_MS:
                    state.markout_5s_bps.append(bps)
                    fill.markout_5s_resolved = True
                elif horizon_ms == MARKOUT_HORIZON_30S_MS:
                    state.markout_30s_bps.append(bps)
                    fill.markout_30s_resolved = True
                pending.remaining_horizons_ms.discard(horizon_ms)
        elif now_ms > target_ms + MARKOUT_EXPIRE_AFTER_MS:
            pending.remaining_horizons_ms.discard(horizon_ms)


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
