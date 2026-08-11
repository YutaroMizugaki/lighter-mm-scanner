"""Generate lightweight aggregate JSON for the Vercel dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lighter_mm.analytics.aggregation import AnalysisSources, analyze_range, analyze_window, scored_to_records
from lighter_mm.analytics.estimated_fill_policy import DEFAULT_ORDER_USD
from lighter_mm.cloud.health import (
    _ms_to_iso,
    _ws_connected,
    build_collector_status_payload,
    build_data_diagnostics,
    collector_status_label,
    latest_data_timestamp_iso,
)
from lighter_mm.config import Settings
from lighter_mm.scoring import ScoredMarket
from lighter_mm.storage.state import RunState

# Backward-compatible re-exports.
__all__ = [
    "collector_status_label",
    "build_collector_status_payload",
    "build_dashboard_payload",
]

def build_dashboard_payload(
    settings: Settings,
    *,
    hours: float | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    state: RunState | None = None,
    sources: AnalysisSources | None = None,
    analysis_result: dict[str, Any] | None = None,
    storage_estimate: dict[str, Any] | None = None,
    ws_runtime: dict[str, Any] | None = None,
    last_book_sample_at_ms: int | None = None,
    last_book_row_at_ms: int | None = None,
    trades_without_reference_mid: int = 0,
    analysis_window_hours: float | None = None,
    run_observation_hours: float | None = None,
) -> dict[str, Any]:
    if analysis_result is None:
        if start_ms is not None and end_ms is not None:
            analysis_result = analyze_range(
                settings,
                start_ms=start_ms,
                end_ms=end_ms,
                sources=sources,
                market_lifecycle=state.market_lifecycle if state is not None else None,
            )
        else:
            analysis_result = analyze_window(
                settings, hours or 72.0, sources=sources
            )
    result = analysis_result
    scored: list[ScoredMarket] = result.get("scored") or []
    screened_rows: list[dict[str, Any]] = result.get("screened") or []
    candidates = [s for s in scored if s.candidate]
    avoid = result.get("avoid") or []
    analysis_error = result.get("error")
    if not analysis_error and state and state.samples_written > 0 and not scored:
        analysis_error = "analysis returned 0 markets despite samples_written > 0"
    markets_discovered = len(state.markets) if state else 0

    health_warnings: list[str] = []

    parquet_health = result.get("parquet_health") or {}
    if parquet_health.get("status") == "degraded":
        corrupt_n = int(parquet_health.get("corrupt_parquet_files") or 0)
        health_warnings.append(
            f"{corrupt_n} corrupted Parquet file(s) skipped during analysis."
        )

    if analysis_error:
        health_warnings.append(str(analysis_error))
    if state and state.samples_written > 0 and markets_discovered > 0 and len(scored) == 0:
        health_warnings.append(
            f"discovered {markets_discovered} markets but analyzed 0 "
            "(check Parquet source paths / GCS mount)"
        )

    coverage_vals = [
        s.row.get("observation_coverage_pct") or s.row.get("data_coverage_pct")
        for s in scored
        if (s.row.get("observation_coverage_pct") or s.row.get("data_coverage_pct")) is not None
    ]
    coverage = sum(coverage_vals) / len(coverage_vals) if coverage_vals else None

    if coverage is not None and coverage < 80.0:
        health_warnings.append(
            f"Low data coverage: {coverage:.1f}%. WebSocket connectivity or "
            "subscription limits may be affecting collection."
        )
    if coverage is not None and coverage < settings.min_coverage_pct:
        health_warnings.append(
            "Candidate ranking is provisional because observation coverage "
            f"is below the required {settings.min_coverage_pct:.0f}%."
        )

    markout_trade_inconsistent = 0
    for s in scored:
        r = s.row
        if (
            r.get("maker_markout_5s_median_bps") is not None
            and (r.get("total_trade_count") or 0) == 0
        ):
            markout_trade_inconsistent += 1
    if markout_trade_inconsistent:
        health_warnings.append(
            f"markout exists but trade count is zero on {markout_trade_inconsistent} "
            "market(s); possible trade aggregation inconsistency"
        )

    if analysis_error:
        analysis_status = "ERROR"
    elif parquet_health.get("status") == "degraded":
        analysis_status = "DEGRADED"
    elif not scored:
        analysis_status = "DEGRADED"
    else:
        analysis_status = "OK"

    collector_status = "UNKNOWN"
    if state is not None:
        collector_status = collector_status_label(
            state,
            ok_minutes=settings.status_ok_minutes,
            warn_minutes=settings.status_warn_minutes,
            startup_grace_minutes=settings.collector_startup_grace_minutes,
            consecutive_sync_failures=0,
        )

    obs_hours = None
    window_hours = result.get("hours") or hours or 72.0
    if run_observation_hours is not None:
        obs_hours = float(run_observation_hours)
    elif state and state.started_at:
        try:
            started = datetime.fromisoformat(state.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if end_ms is not None:
                end_dt = datetime.fromtimestamp(end_ms / 1000.0, tz=UTC)
                obs_hours = (end_dt - started).total_seconds() / 3600.0
            elif state.status == "completed" and state.ended_at:
                ended = datetime.fromisoformat(state.ended_at)
                if ended.tzinfo is None:
                    ended = ended.replace(tzinfo=UTC)
                obs_hours = (ended - started).total_seconds() / 3600.0
            else:
                obs_hours = (datetime.now(UTC) - started).total_seconds() / 3600.0
        except ValueError:
            obs_hours = window_hours

    if analysis_window_hours is not None:
        ranked_window_hours = float(analysis_window_hours)
    elif start_ms is not None and end_ms is not None:
        ranked_window_hours = max((end_ms - start_ms) / 3_600_000.0, 0.0)
    else:
        ranked_window_hours = float(window_hours)

    top = candidates[0] if candidates else None
    generated_at = datetime.now(UTC).isoformat()
    last_trade_at = _ms_to_iso(state.last_trade_timestamp_ms if state else None)
    last_usable_book = _ms_to_iso(last_book_sample_at_ms)
    last_book_row = _ms_to_iso(last_book_row_at_ms)
    latest_parquet_event_ms = result.get("latest_book_event_ms")
    last_update = latest_data_timestamp_iso(
        last_book_sample_at_ms=last_book_sample_at_ms,
        last_trade_at_ms=state.last_trade_timestamp_ms if state else None,
        latest_parquet_event_ms=latest_parquet_event_ms,
        last_durable_event_ms=state.last_durable_event_ms if state else None,
    )

    overview = {
        "title": "Lighter MM Scanner",
        "status": analysis_status,
        "collector_status": collector_status,
        "analysis_status": analysis_status,
        "run_id": state.run_id if state else None,
        "started_at": state.started_at if state else None,
        "observation_hours": obs_hours,
        "run_observation_hours": obs_hours,
        "analysis_scope": "rolling",
        "analysis_window_hours": ranked_window_hours,
        "observation_target_hours": state.observation_target_hours if state else window_hours,
        "markets": len(scored) + len(screened_rows),
        "markets_analyzed": len(scored),
        "markets_discovered": markets_discovered,
        "candidates": len(candidates),
        "coverage_pct": coverage,
        "last_update": last_update,
        "last_data_at": last_update,
        "latest_market_event_at": _ms_to_iso(latest_parquet_event_ms),
        "last_successful_sync": state.last_successful_flush if state else None,
        "last_successful_flush": state.last_successful_flush if state else None,
        "last_trade_at": last_trade_at,
        "last_book_sample_at": last_usable_book,
        "last_usable_book_sample_at": last_usable_book,
        "last_book_row_at": last_book_row,
        "trades_without_reference_mid": trades_without_reference_mid,
        "git_sha": state.git_sha if state else settings.git_sha,
        "collector_version": state.collector_version if state else settings.collector_version,
        "top_candidate": _market_card(top) if top else None,
        "analysis_error": analysis_error,
        "health_warnings": health_warnings,
        "samples_written": state.samples_written if state else 0,
        "storage_estimate": storage_estimate,
        "ws": ws_runtime,
        "generated_at": generated_at,
        "read_only": True,
        "diagnostics": build_data_diagnostics(
            collector_status=collector_status,
            last_event_at=last_update,
            last_flush_at=state.last_successful_flush if state else None,
            connected=_ws_connected(ws_runtime),
            analysis_status=analysis_status,
            analysis_completed_at=generated_at if not analysis_error else None,
            valid_parquet_files=int(parquet_health.get("valid_parquet_files") or 0),
            invalid_parquet_files=int(parquet_health.get("corrupt_parquet_files") or 0),
        ),
        "disclaimer": (
            "読み取り専用のリサーチツールです。売買・ウォレット接続・APIキーは使用しません。"
            "表示されるスプレッドや取引回数は利益を保証するものではありません。"
            "実際のマーケットメイクでは、約定確率・逆選択・在庫リスクを別途検証してください。"
            " Paper MM is a historical simulation based on sampled public market data. "
            "It does not reproduce actual exchange queue position, latency, order acknowledgement, "
            "cancellations, hidden liquidity, network delays, or exact fill probability. "
            "No real orders are placed."
        ),
    }

    markets = []
    for s in scored:
        markets.append(_market_row(s))
    for row in screened_rows:
        markets.append(row)

    market_details = {str(s.row.get("symbol")): _market_detail(s) for s in scored}

    return {
        "latest": overview,
        "markets": markets,
        "candidates": [_market_row(s) for s in candidates],
        "avoid": [_market_row(s) for s in avoid],
        "market_details": market_details,
        "raw_records": scored_to_records(scored)[:500],
        "analysis_error": analysis_error,
    }


def _confidence_payload(s: ScoredMarket) -> dict[str, Any]:
    return {
        "raw_score": round(s.raw_score, 2),
        "confidence": round(s.confidence, 4),
        "effective_score": round(s.effective_score, 2),
        "confidence_label": s.confidence_label,
        "confidence_reasons": list(s.confidence_reasons),
        "confidence_breakdown": dict(s.confidence_breakdown),
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
        **_confidence_payload(s),
    }


def _market_row(s: ScoredMarket) -> dict[str, Any]:
    r = s.row
    warnings = list(s.warnings or [])
    if (
        r.get("maker_markout_5s_median_bps") is not None
        and (r.get("total_trade_count") or 0) == 0
    ):
        warnings.append(
            "markout exists but trade count is zero; possible trade aggregation inconsistency"
        )
    return {
        "symbol": r.get("symbol"),
        "market_id": r.get("market_id"),
        "analysis_stage": r.get("analysis_stage", "full"),
        "stage1": r.get("stage1"),
        "score": round(s.score, 2),
        "letter_rank": s.letter_rank,
        "is_candidate": s.candidate,
        "median_spread_bps": r.get("median_spread_bps"),
        "pct_time_spread_ge_5bps": r.get("pct_time_spread_ge_5bps"),
        "median_two_sided_depth_10bps_usd": r.get("median_two_sided_depth_10bps_usd"),
        "trades_per_minute_median": r.get("trades_per_minute_median"),
        "trades_per_minute_mean": r.get("trades_per_minute_mean"),
        "total_trade_count": r.get("total_trade_count"),
        "markout_5s_count": r.get("markout_5s_count"),
        "markout_30s_count": r.get("markout_30s_count"),
        "markout_sample_quality": r.get("markout_sample_quality"),
        "maker_markout_5s_median_bps": r.get("maker_markout_5s_median_bps"),
        "maker_markout_30s_median_bps": r.get("maker_markout_30s_median_bps"),
        "estimated_maker_fill_rate_5s_conservative": r.get(
            "estimated_maker_fill_rate_5s_conservative"
        ),
        "estimated_maker_fill_rate_30s_conservative": r.get(
            "estimated_maker_fill_rate_30s_conservative"
        ),
        "estimated_maker_fill_rate_5s_optimistic": r.get(
            "estimated_maker_fill_rate_5s_optimistic"
        ),
        "estimated_maker_fill_rate_30s_optimistic": r.get(
            "estimated_maker_fill_rate_30s_optimistic"
        ),
        "estimated_maker_fill_samples": r.get("estimated_maker_fill_samples"),
        "estimated_maker_fill_sample_quality": r.get(
            "estimated_maker_fill_sample_quality"
        ),
        "estimated_maker_edge_5s_bps": r.get("estimated_maker_edge_5s_bps"),
        "estimated_maker_edge_30s_bps": r.get("estimated_maker_edge_30s_bps"),
        "estimated_maker_edge_fee_included": r.get(
            "estimated_maker_edge_fee_included"
        ),
        "paper_mm_total_pnl_usd": r.get("paper_mm_total_pnl_usd"),
        "paper_mm_round_trips": r.get("paper_mm_round_trips"),
        "paper_mm_filled_notional_usd": r.get("paper_mm_filled_notional_usd"),
        "paper_mm_pnl_per_hour_usd": r.get("paper_mm_pnl_per_hour_usd"),
        "paper_mm_pnl_bps_on_filled_notional": r.get("paper_mm_pnl_bps_on_filled_notional"),
        "paper_mm_max_abs_inventory_usd": r.get("paper_mm_max_abs_inventory_usd"),
        "paper_mm_time_with_inventory_pct": r.get("paper_mm_time_with_inventory_pct"),
        "paper_mm_median_holding_seconds": r.get("paper_mm_median_holding_seconds"),
        "paper_mm_markout_5s_median_bps": r.get("paper_mm_markout_5s_median_bps"),
        "paper_mm_markout_30s_median_bps": r.get("paper_mm_markout_30s_median_bps"),
        "paper_mm_status": r.get("paper_mm_status"),
        "analysis_scope": r.get("analysis_scope"),
        "analysis_window_hours": r.get("analysis_window_hours"),
        "current_funding_rate": r.get("current_funding_rate"),
        "data_coverage_pct": r.get("data_coverage_pct"),
        "observation_coverage_pct": r.get("observation_coverage_pct"),
        "usable_quote_coverage_pct": r.get("usable_quote_coverage_pct"),
        "spread_coverage_pct": r.get("spread_coverage_pct"),
        "observed_samples": r.get("observed_samples"),
        "expected_samples": r.get("expected_samples"),
        "observation_hours": r.get("observation_hours"),
        "latest_book_update_age_seconds": r.get("latest_book_update_age_seconds"),
        "p95_book_update_age_seconds": r.get("p95_book_update_age_seconds"),
        "recommended_max_order_usd": s.recommended_max_order_usd,
        "warnings": warnings,
        "pros": s.pros[:6],
        "cons": s.cons[:6],
        **_confidence_payload(s),
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
            "trades_per_minute_mean": r.get("trades_per_minute_mean"),
            "total_quote_volume": r.get("total_quote_volume"),
            "markout_5s_count": r.get("markout_5s_count"),
            "markout_30s_count": r.get("markout_30s_count"),
            "p50_abs_mid_move_5s_bps": r.get("p50_abs_mid_move_5s_bps"),
            "p95_abs_mid_move_5s_bps": r.get("p95_abs_mid_move_5s_bps"),
            "p50_abs_mid_move_30s_bps": r.get("p50_abs_mid_move_30s_bps"),
            "p95_abs_mid_move_30s_bps": r.get("p95_abs_mid_move_30s_bps"),
            "open_interest": r.get("open_interest"),
            "daily_quote_volume_usd": r.get("daily_quote_volume_usd"),
            "funding_rate": r.get("funding_rate"),
            "data_coverage_pct": r.get("data_coverage_pct"),
            "observation_coverage_pct": r.get("observation_coverage_pct"),
            "usable_quote_coverage_pct": r.get("usable_quote_coverage_pct"),
            "spread_coverage_pct": r.get("spread_coverage_pct"),
            "observed_samples": r.get("observed_samples"),
            "expected_samples": r.get("expected_samples"),
            "observation_hours": r.get("observation_hours"),
            "latest_book_update_age_seconds": r.get("latest_book_update_age_seconds"),
            "p95_book_update_age_seconds": r.get("p95_book_update_age_seconds"),
            "size_fit": s.size_fit,
            "penalties": s.penalties,
            "estimated_maker_fill_by_size": r.get("estimated_maker_fill_by_size"),
            "estimated_maker_fill_order_usd_default": DEFAULT_ORDER_USD,
            "paper_mm_order_usd": r.get("paper_mm_order_usd"),
            "paper_mm_queue_model": r.get("paper_mm_queue_model"),
            "paper_mm_quote_count": r.get("paper_mm_quote_count"),
            "paper_mm_bid_fills": r.get("paper_mm_bid_fills"),
            "paper_mm_ask_fills": r.get("paper_mm_ask_fills"),
            "paper_mm_partial_fills": r.get("paper_mm_partial_fills"),
            "paper_mm_full_fills": r.get("paper_mm_full_fills"),
            "paper_mm_filled_notional_usd": r.get("paper_mm_filled_notional_usd"),
            "paper_mm_round_trips": r.get("paper_mm_round_trips"),
            "paper_mm_gross_pnl_usd": r.get("paper_mm_gross_pnl_usd"),
            "paper_mm_realized_pnl_usd": r.get("paper_mm_realized_pnl_usd"),
            "paper_mm_unrealized_pnl_usd": r.get("paper_mm_unrealized_pnl_usd"),
            "paper_mm_fees_usd": r.get("paper_mm_fees_usd"),
            "paper_mm_total_pnl_usd": r.get("paper_mm_total_pnl_usd"),
            "paper_mm_pnl_per_hour_usd": r.get("paper_mm_pnl_per_hour_usd"),
            "paper_mm_max_abs_inventory_usd": r.get("paper_mm_max_abs_inventory_usd"),
            "paper_mm_time_with_inventory_pct": r.get("paper_mm_time_with_inventory_pct"),
            "paper_mm_median_holding_seconds": r.get("paper_mm_median_holding_seconds"),
            "paper_mm_p90_holding_seconds": r.get("paper_mm_p90_holding_seconds"),
            "paper_mm_max_holding_seconds": r.get("paper_mm_max_holding_seconds"),
            "paper_mm_markout_5s_median_bps": r.get("paper_mm_markout_5s_median_bps"),
            "paper_mm_markout_30s_median_bps": r.get("paper_mm_markout_30s_median_bps"),
            "paper_mm_markout_5s_count": r.get("paper_mm_markout_5s_count"),
            "paper_mm_markout_30s_count": r.get("paper_mm_markout_30s_count"),
            "paper_mm_final_inventory_usd": r.get("paper_mm_final_inventory_usd"),
            "paper_mm_fee_included": r.get("paper_mm_fee_included"),
            "paper_mm_pnl_bps_on_filled_notional": r.get("paper_mm_pnl_bps_on_filled_notional"),
            "paper_mm_samples": r.get("paper_mm_samples"),
            "paper_mm_status": r.get("paper_mm_status"),
            "paper_mm_gross_spread_capture_usd": r.get("paper_mm_gross_spread_capture_usd"),
            "confidence_reasons": list(s.confidence_reasons),
        }
    )
    return base
