"""Reference mid selection for maker markout (past-only, no look-ahead)."""

from __future__ import annotations

from lighter_mm.engine.mid_history import MidHistory


def reference_mid_for_trade(
    hist: MidHistory | None,
    trade_timestamp_ms: int,
    *,
    max_age_ms: int = 3000,
) -> float | None:
    """Return reference mid at or before trade time within max_age_ms.

  Never uses future mids (no mid_at / mid_at_or_after / nearest_at_or_after).
    """
    if hist is None:
        return None
    pt = hist.nearest_at_or_before(trade_timestamp_ms)
    if pt is None:
        return None
    age = trade_timestamp_ms - pt.ts_ms
    if age < 0 or age > max_age_ms:
        return None
    return pt.mid
