"""Paper MM metrics and empty result templates."""

from __future__ import annotations

from typing import Any

from lighter_mm.config import Settings
from lighter_mm.paper_mm.models import PaperMmConfig


def paper_mm_config_from_settings(
    settings: Settings,
    *,
    order_usd_override: float | None = None,
) -> PaperMmConfig:
    return PaperMmConfig(
        order_usd=order_usd_override if order_usd_override is not None else settings.paper_mm_order_usd,
        max_inventory_usd=settings.paper_mm_max_inventory_usd,
        max_quote_age_seconds=settings.paper_mm_max_quote_age_seconds,
        maker_fee_bps=settings.paper_mm_maker_fee_bps,
    )


def empty_paper_mm_result(
    settings: Settings,
    *,
    status: str = "not_simulated",
    order_usd_override: float | None = None,
) -> dict[str, Any]:
    order_usd = (
        order_usd_override if order_usd_override is not None else settings.paper_mm_order_usd
    )
    fee_included = settings.paper_mm_maker_fee_bps is not None
    return {
        "paper_mm_enabled": settings.paper_mm_enabled,
        "paper_mm_order_usd": order_usd,
        "paper_mm_queue_model": "conservative_touch_ahead",
        "paper_mm_quote_count": None,
        "paper_mm_bid_fills": None,
        "paper_mm_ask_fills": None,
        "paper_mm_partial_fills": None,
        "paper_mm_full_fills": None,
        "paper_mm_filled_notional_usd": None,
        "paper_mm_round_trips": None,
        "paper_mm_gross_pnl_usd": None,
        "paper_mm_realized_pnl_usd": None,
        "paper_mm_unrealized_pnl_usd": None,
        "paper_mm_fees_usd": None,
        "paper_mm_total_pnl_usd": None,
        "paper_mm_pnl_per_hour_usd": None,
        "paper_mm_max_abs_inventory_usd": None,
        "paper_mm_time_with_inventory_pct": None,
        "paper_mm_median_holding_seconds": None,
        "paper_mm_p90_holding_seconds": None,
        "paper_mm_max_holding_seconds": None,
        "paper_mm_markout_5s_median_bps": None,
        "paper_mm_markout_30s_median_bps": None,
        "paper_mm_markout_5s_count": None,
        "paper_mm_markout_30s_count": None,
        "paper_mm_final_inventory_usd": None,
        "paper_mm_fee_included": fee_included if status == "ok" else False,
        "paper_mm_pnl_bps_on_filled_notional": None,
        "paper_mm_samples": None,
        "paper_mm_status": status,
    }


def attach_paper_mm_row(row: dict[str, Any], paper: dict[str, Any]) -> None:
    row.update(paper)
