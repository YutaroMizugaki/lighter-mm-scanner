"""Durable per-market active lifecycle timestamps for coverage windows."""

from __future__ import annotations

from lighter_mm.models import MarketMeta
from lighter_mm.storage.state import MarketLifecycleEntry, RunState


def record_market_added(state: RunState, market_id: int, at_ms: int) -> None:
    """Record first active time or a new active episode after removal."""
    entry = state.market_lifecycle.get(market_id)
    if entry is None or entry.removed_at_ms is not None:
        state.market_lifecycle[market_id] = MarketLifecycleEntry(
            first_active_at_ms=at_ms,
            removed_at_ms=None,
        )


def record_market_removed(state: RunState, market_id: int, at_ms: int) -> None:
    """Record inactive/removal time without inferring from Parquet row timestamps."""
    entry = state.market_lifecycle.get(market_id)
    if entry is None:
        state.market_lifecycle[market_id] = MarketLifecycleEntry(
            first_active_at_ms=at_ms,
            removed_at_ms=at_ms,
        )
    elif entry.removed_at_ms is None:
        entry.removed_at_ms = at_ms


def apply_discovery_markets(state: RunState, markets: list[MarketMeta], at_ms: int) -> None:
    """Initial discovery: mark all markets active at ``at_ms``."""
    for market in markets:
        record_market_added(state, market.market_id, at_ms)


def apply_market_refresh(
    state: RunState,
    added: list[MarketMeta],
    removed: list[MarketMeta],
    at_ms: int,
) -> None:
    """Update lifecycle on periodic market refresh."""
    for market in added:
        record_market_added(state, market.market_id, at_ms)
    for market in removed:
        record_market_removed(state, market.market_id, at_ms)
