"""Generate lightweight aggregate JSON for the Vercel dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lighter_mm.analytics.aggregation import AnalysisSources, analyze_range, analyze_window, scored_to_records
from lighter_mm.config import Settings
from lighter_mm.scoring import ScoredMarket
from lighter_mm.storage.state import RunState


def collector_status_label(
    state: RunState | None,
    *,
    ok_minutes: float,
    warn_minutes: float,
    degraded: bool = False,
) -> str:
    """Collector health label (independent of analysis freshness)."""
    if state is None:
        return "ERROR"
    if state.status == "completed":
        return "COMPLETED"
    if state.status == "error":
        return "ERROR"
    flush = state.last_successful_flush or state.updated_at or state.started_at
    if not flush:
        return "ERROR"
    try:
        ts = datetime.fromisoformat(flush)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except ValueError:
        return "ERROR"
    age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
    if degraded and state.status == "running" and age_min <= ok_minutes:
        return "DEGRADED"
    if state.status == "running" and age_min <= ok_minutes:
        return "COLLECTING"
    if age_min <= warn_minutes:
        return "STALE"
    return "OFFLINE" if state.status == "running" else state.status.upper()


def build_collector_status_payload(
    state: RunState,
    *,
    settings: Settings,
    ws_runtime: dict[str, Any] | None = None,
    last_book_sample_at_ms: int | None = None,
    last_book_row_at_ms: int | None = None,
    trades_without_reference_mid: int = 0,
    health_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Collector-only health JSON (never writes ranking aggregates)."""
    warnings = list(health_warnings or [])
    warnings.extend(_ws_degraded(ws_runtime))
    if last_book_sample_at_ms is not None:
        age_s = (datetime.now(UTC).timestamp() * 1000 - last_book_sample_at_ms) / 1000.0
        stale_threshold_s = max(settings.book_sample_interval_seconds * 3, 30)
        if age_s > stale_threshold_s:
            warnings.append(
                f"Usable book samples stale: last sample {age_s:.0f}s ago "
                f"(threshold {stale_threshold_s:.0f}s)."
            )
    degraded = bool(_ws_degraded(ws_runtime)) or any(
        "stale" in w.lower() for w in warnings
    )
    status = collector_status_label(
        state,
        ok_minutes=settings.status_ok_minutes,
        warn_minutes=settings.status_warn_minutes,
        degraded=degraded,
    )
    return {
        "run_id": state.run_id,
        "status": status,
        "started_at": state.started_at,
        "ended_at": state.ended_at,
        "generated_at": datetime.now(UTC).isoformat(),
        "last_successful_sync": state.last_successful_flush,
        "samples_written": state.samples_written,
        "trades_written": state.trades_written,
        "markouts_written": state.markouts_written,
        "last_trade_at": _ms_to_iso(state.last_trade_timestamp_ms),
        "last_usable_book_sample_at": _ms_to_iso(last_book_sample_at_ms),
        "last_book_row_at": _ms_to_iso(last_book_row_at_ms),
        "trades_without_reference_mid": trades_without_reference_mid,
        "ws": ws_runtime,
        "health_warnings": warnings,
        "git_sha": state.git_sha or settings.git_sha,
        "collector_version": state.collector_version or settings.collector_version,
    }


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _ws_degraded(ws_runtime: dict[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    if not ws_runtime:
        return warnings
    connected = int(ws_runtime.get("connected_shards") or 0)
    total = int(ws_runtime.get("total_shards") or 0)
    planned = int(ws_runtime.get("planned_channels") or ws_runtime.get("subscribed_channels") or 0)
    acked = int(ws_runtime.get("acked_channels") or ws_runtime.get("subscribed_channels") or 0)
    if total > 0 and connected < total:
        warnings.append(f"WebSocket degraded: {connected}/{total} shards connected.")
    if planned > 0 and acked < planned:
        warnings.append(f"Subscription ACK incomplete: {acked}/{planned} channels acked.")
    return warnings


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
) -> dict[str, Any]:
    if analysis_result is None:
        if start_ms is not None and end_ms is not None:
            analysis_result = analyze_range(
                settings,
                start_ms=start_ms,
                end_ms=end_ms,
                sources=sources,
            )
        else:
            analysis_result = analyze_window(
                settings, hours or 72.0, sources=sources
            )
    result = analysis_result
    scored: list[ScoredMarket] = result.get("scored") or []
    candidates = [s for s in scored if s.candidate]
    avoid = result.get("avoid") or []
    analysis_error = result.get("error")
    if not analysis_error and state and state.samples_written > 0 and not scored:
        analysis_error = "analysis returned 0 markets despite samples_written > 0"
    markets_discovered = len(state.markets) if state else 0

    health_warnings: list[str] = []

    if analysis_error:
        health_warnings.append(str(analysis_error))
    if state and state.samples_written > 0 and markets_discovered > 0 and len(scored) == 0:
        health_warnings.append(
            f"discovered {markets_discovered} markets but analyzed 0 "
            "(check Parquet source paths / GCS mount)"
        )

    coverage_vals = [
        s.row.get("data_coverage_pct")
        for s in scored
        if s.row.get("data_coverage_pct") is not None
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
        status = "ERROR"
    elif not scored:
        status = "DEGRADED"
    else:
        status = "OK"

    obs_hours = None
    window_hours = result.get("hours") or hours or 72.0
    if state and state.started_at:
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

    top = candidates[0] if candidates else None
    generated_at = datetime.now(UTC).isoformat()
    last_trade_at = _ms_to_iso(state.last_trade_timestamp_ms if state else None)
    last_usable_book = _ms_to_iso(last_book_sample_at_ms)
    last_book_row = _ms_to_iso(last_book_row_at_ms)

    overview = {
        "title": "Lighter MM Scanner",
        "status": status,
        "run_id": state.run_id if state else None,
        "started_at": state.started_at if state else None,
        "observation_hours": obs_hours,
        "observation_target_hours": state.observation_target_hours if state else window_hours,
        "markets": len(scored),
        "markets_analyzed": len(scored),
        "markets_discovered": markets_discovered,
        "candidates": len(candidates),
        "coverage_pct": coverage,
        "last_update": generated_at,
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
        "analysis_error": analysis_error,
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
        "maker_markout_5s_median_bps": r.get("maker_markout_5s_median_bps"),
        "maker_markout_30s_median_bps": r.get("maker_markout_30s_median_bps"),
        "current_funding_rate": r.get("current_funding_rate"),
        "data_coverage_pct": r.get("data_coverage_pct"),
        "observed_samples": r.get("observed_samples"),
        "expected_samples": r.get("expected_samples"),
        "observation_hours": r.get("observation_hours"),
        "latest_book_update_age_seconds": r.get("latest_book_update_age_seconds"),
        "p95_book_update_age_seconds": r.get("p95_book_update_age_seconds"),
        "recommended_max_order_usd": s.recommended_max_order_usd,
        "warnings": warnings,
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
            "observed_samples": r.get("observed_samples"),
            "expected_samples": r.get("expected_samples"),
            "observation_hours": r.get("observation_hours"),
            "latest_book_update_age_seconds": r.get("latest_book_update_age_seconds"),
            "p95_book_update_age_seconds": r.get("p95_book_update_age_seconds"),
            "size_fit": s.size_fit,
            "penalties": s.penalties,
        }
    )
    return base
