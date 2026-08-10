"""Paper Market Maker — historical conservative queue simulation."""

from __future__ import annotations

import logging
from typing import Any

import duckdb

from lighter_mm.config import Settings
from lighter_mm.paper_mm.metrics import (
    attach_paper_mm_row,
    empty_paper_mm_result,
    paper_mm_config_from_settings,
)
from lighter_mm.paper_mm.replay import run_paper_mm_replay
from lighter_mm.scoring import ScoredMarket

log = logging.getLogger(__name__)

__all__ = [
    "run_paper_mm_for_scored",
    "run_paper_mm_market",
    "resolve_paper_mm_targets",
]


def resolve_paper_mm_targets(
    scored: list[ScoredMarket],
    *,
    top_n: int,
    market_ids_override: set[int] | None = None,
) -> list[ScoredMarket]:
    if market_ids_override is not None:
        override_set = market_ids_override
        return [s for s in scored if int(s.row.get("market_id") or -1) in override_set]
    return scored[:top_n]


def run_paper_mm_market(
    con: duckdb.DuckDBPyConnection,
    market_id: int,
    settings: Settings,
    start_ms: int,
    end_ms: int,
    window_hours: float,
    *,
    order_usd_override: float | None = None,
) -> dict[str, Any]:
    config = paper_mm_config_from_settings(settings, order_usd_override=order_usd_override)
    try:
        con.execute("SELECT 1 FROM book_observed LIMIT 1")
        con.execute("SELECT 1 FROM trade_deduped LIMIT 1")
    except Exception:
        return empty_paper_mm_result(settings, status="unavailable", order_usd_override=order_usd_override)

    return run_paper_mm_replay(
        con,
        market_id,
        start_ms,
        end_ms,
        config,
        window_hours,
    )


def run_paper_mm_for_scored(
    con: duckdb.DuckDBPyConnection,
    scored: list[ScoredMarket],
    settings: Settings,
    start_ms: int,
    end_ms: int,
    window_hours: float,
    *,
    market_ids_override: set[int] | None = None,
    top_n_override: int | None = None,
    order_usd_override: float | None = None,
) -> None:
    if not settings.paper_mm_enabled:
        return

    top_n = top_n_override if top_n_override is not None else settings.paper_mm_top_n
    targets = resolve_paper_mm_targets(
        scored,
        top_n=top_n,
        market_ids_override=market_ids_override,
    )
    target_ids = {int(s.row.get("market_id") or -1) for s in targets}

    for s in scored:
        mid = int(s.row.get("market_id") or -1)
        if mid not in target_ids:
            attach_paper_mm_row(s.row, empty_paper_mm_result(settings, status="not_simulated"))

    for s in targets:
        mid = int(s.row.get("market_id") or -1)
        try:
            result = run_paper_mm_market(
                con,
                mid,
                settings,
                start_ms,
                end_ms,
                window_hours,
                order_usd_override=order_usd_override,
            )
            attach_paper_mm_row(s.row, result)
        except Exception:
            log.exception("paper_mm failed market_id=%s", mid)
            attach_paper_mm_row(
                s.row,
                empty_paper_mm_result(settings, status="error", order_usd_override=order_usd_override),
            )
