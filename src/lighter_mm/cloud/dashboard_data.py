"""Generate lightweight aggregate JSON for the Vercel dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lighter_mm.analytics.aggregation import analyze_window, scored_to_records
from lighter_mm.config import Settings
from lighter_mm.scoring import ScoredMarket
from lighter_mm.storage.state import RunState


def collector_status_label(
    state: RunState | None,
    *,
    ok_minutes: float,
    warn_minutes: float,
) -> str:
    if state is None:
        return "ERROR"
    if state.status == "completed":
        return "COMPLETED"
    if state.status == "error":
        return "ERROR"
    if state.status in {"stopped"} and state.status != "running":
        # stopped mid-run without completion
        pass
    flush = state.last_successful_flush or state.updated_at or state.started_at
    if not flush:
        return "ERROR"
    try:
        ts = datetime.fromisoformat(flush)
    except ValueError:
        return "ERROR"
    age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
    if state.status == "running" and age_min <= ok_minutes:
        return "COLLECTING"
    if age_min <= warn_minutes:
        return "STALE"
    return "OFFLINE" if state.status == "running" else state.status.upper()


def build_dashboard_payload(
    settings: Settings,
    *,
    hours: float,
    state: RunState | None,
    storage_estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = analyze_window(settings, hours)
    scored: list[ScoredMarket] = result.get("scored") or []
    candidates = [s for s in scored if s.candidate]
    avoid = result.get("avoid") or []
    status = collector_status_label(
        state,
        ok_minutes=settings.status_ok_minutes,
        warn_minutes=settings.status_warn_minutes,
    )
    obs_hours = None
    if state and state.started_at:
        try:
            started = datetime.fromisoformat(state.started_at)
            obs_hours = (datetime.now(UTC) - started).total_seconds() / 3600.0
        except ValueError:
            obs_hours = hours

    coverage_vals = [
        s.row.get("data_coverage_pct")
        for s in scored
        if s.row.get("data_coverage_pct") is not None
    ]
    coverage = sum(coverage_vals) / len(coverage_vals) if coverage_vals else None

    top = scored[0] if scored else None
    overview = {
        "title": "Lighter MM Scanner",
        "status": status,
        "run_id": state.run_id if state else None,
        "started_at": state.started_at if state else None,
        "observation_hours": obs_hours,
        "observation_target_hours": state.observation_target_hours if state else hours,
        "markets": len(scored),
        "candidates": len(candidates),
        "coverage_pct": coverage,
        "last_update": state.last_successful_flush if state else None,
        "git_sha": state.git_sha if state else settings.git_sha,
        "collector_version": state.collector_version if state else settings.collector_version,
        "top_candidate": _market_card(top) if top else None,
        "storage_estimate": storage_estimate,
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "disclaimer": (
            "READ-ONLY research. Displayed spread × trade count ≠ profit. "
            "No trading / no wallet / no API keys."
        ),
    }

    markets = []
    for s in scored:
        markets.append(_market_row(s))

    market_details = {str(s.row.get("symbol")): _market_detail(s) for s in scored}

    return {
        "latest": overview,
        "markets": markets,
        "candidates": [_market_row(s) for s in candidates],
        "avoid": [_market_row(s) for s in avoid],
        "market_details": market_details,
        "raw_records": scored_to_records(scored)[:500],
    }


def _market_card(s: ScoredMarket) -> dict[str, Any]:
    r = s.row
    return {
        "symbol": r.get("symbol"),
        "market_id": r.get("market_id"),
        "score": round(s.score, 2),
        "letter_rank": s.letter_rank,
        "median_spread_bps": r.get("median_spread_bps"),
        "maker_markout_5s_median_bps": r.get("maker_markout_5s_median_bps"),
        "maker_markout_30s_median_bps": r.get("maker_markout_30s_median_bps"),
    }


def _market_row(s: ScoredMarket) -> dict[str, Any]:
    r = s.row
    return {
        "symbol": r.get("symbol"),
        "market_id": r.get("market_id"),
        "score": round(s.score, 2),
        "letter_rank": s.letter_rank,
        "is_candidate": s.candidate,
        "median_spread_bps": r.get("median_spread_bps"),
        "pct_time_spread_ge_5bps": r.get("pct_time_spread_ge_5bps"),
        "median_two_sided_depth_10bps_usd": r.get("median_two_sided_depth_10bps_usd"),
        "trades_per_minute_median": r.get("trades_per_minute_median"),
        "maker_markout_5s_median_bps": r.get("maker_markout_5s_median_bps"),
        "maker_markout_30s_median_bps": r.get("maker_markout_30s_median_bps"),
        "current_funding_rate": r.get("current_funding_rate"),
        "data_coverage_pct": r.get("data_coverage_pct"),
        "recommended_max_order_usd": s.recommended_max_order_usd,
        "warnings": s.warnings,
        "pros": s.pros[:6],
        "cons": s.cons[:6],
    }


def _market_detail(s: ScoredMarket) -> dict[str, Any]:
    base = _market_row(s)
    r = s.row
    base.update(
        {
            "mean_spread_bps": r.get("mean_spread_bps"),
            "p90_spread_bps": r.get("p90_spread_bps"),
            "median_two_sided_depth_5bps_usd": r.get("median_two_sided_depth_5bps_usd"),
            "median_two_sided_depth_25bps_usd": r.get("median_two_sided_depth_25bps_usd"),
            "total_trade_count": r.get("total_trade_count"),
            "total_quote_volume": r.get("total_quote_volume"),
            "p50_abs_mid_move_5s_bps": r.get("p50_abs_mid_move_5s_bps"),
            "p95_abs_mid_move_5s_bps": r.get("p95_abs_mid_move_5s_bps"),
            "p50_abs_mid_move_30s_bps": r.get("p50_abs_mid_move_30s_bps"),
            "p95_abs_mid_move_30s_bps": r.get("p95_abs_mid_move_30s_bps"),
            "open_interest": r.get("open_interest"),
            "daily_quote_volume_usd": r.get("daily_quote_volume_usd"),
            "funding_rate": r.get("funding_rate"),
            "size_fit": s.size_fit,
            "penalties": s.penalties,
        }
    )
    return base
