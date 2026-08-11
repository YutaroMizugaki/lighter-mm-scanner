"""Stage 1 fast screening — lightweight DuckDB aggregation for market selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import duckdb

from lighter_mm.analytics.book_metrics import _spread_persistence_from_relation
from lighter_mm.analytics.book_semantics import (
    BOOK_METRICS_GOOD_PREDICATE,
    BOOK_OBSERVED_PREDICATE,
    EFFECTIVE_SPREAD_EXPR,
)
from lighter_mm.analytics.markout_metrics import _aggregate_markouts_sql
from lighter_mm.analytics.parquet_source import (
    _book_projection,
    _isolate_readable_parquet_files,
    _probe_parquet_columns,
    _read_view,
)
from lighter_mm.config import Settings
from lighter_mm.scoring import _pct_rank
from lighter_mm.storage.state import MarketLifecycleEntry

log = logging.getLogger(__name__)


def _register_market_lifecycle_table(
    con: duckdb.DuckDBPyConnection,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None,
) -> bool:
    con.execute(
        """
        CREATE OR REPLACE TABLE market_lifecycle_tbl (
            market_id BIGINT,
            first_active_at_ms BIGINT,
            removed_at_ms BIGINT
        )
        """
    )
    if market_lifecycle is None:
        return False
    if market_lifecycle:
        rows = [
            (mid, entry.first_active_at_ms, entry.removed_at_ms)
            for mid, entry in market_lifecycle.items()
        ]
        con.executemany(
            "INSERT INTO market_lifecycle_tbl VALUES (?, ?, ?)",
            rows,
        )
    return True


@dataclass
class Stage1Market:
    """Per-market Stage 1 screening metrics."""

    market_id: int
    symbol: str | None = None
    book_observation_count: int = 0
    book_update_count: int = 0
    trade_count: int = 0
    trade_volume: float = 0.0
    observation_coverage: float = 0.0
    book_first_seen_at: int | None = None
    book_last_seen_at: int | None = None
    trade_first_seen_at: int | None = None
    trade_last_seen_at: int | None = None
    median_spread_bps: float | None = None
    mean_spread_bps: float | None = None
    p25_spread_bps: float | None = None
    p75_spread_bps: float | None = None
    median_two_sided_depth_10bps_usd: float | None = None
    pct_time_spread_ge_5bps: float | None = None
    trades_per_minute_median: float | None = None
    trades_per_minute_mean: float | None = None
    maker_markout_5s_median_bps: float | None = None
    observation_hours: float = 0.0
    observation_coverage_pct: float = 0.0
    data_coverage_pct: float = 0.0
    eligible: bool = False
    screening_score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _create_stage1_market_windows_view(
    con: duckdb.DuckDBPyConnection,
    *,
    start_ms: int,
    end_ms: int,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None,
    interval_ms: int,
) -> None:
    """Lifecycle-aware observation bounds from deduped book bounds."""
    has_lifecycle = _register_market_lifecycle_table(con, market_lifecycle)

    if has_lifecycle:
        effective_start_expr = (
            f"CASE WHEN lc.market_id IS NOT NULL "
            f"THEN GREATEST(CAST({start_ms} AS BIGINT), lc.first_active_at_ms) "
            f"ELSE GREATEST(CAST({start_ms} AS BIGINT), bounds.first_observed_ms) END"
        )
        effective_end_expr = (
            f"CASE WHEN lc.market_id IS NOT NULL "
            f"AND (lc.removed_at_ms IS NULL OR lc.removed_at_ms > CAST({end_ms} AS BIGINT)) "
            f"THEN CAST({end_ms} AS BIGINT) "
            f"WHEN lc.market_id IS NOT NULL "
            f"THEN LEAST(CAST({end_ms} AS BIGINT), lc.removed_at_ms) "
            f"ELSE CAST({end_ms} AS BIGINT) END"
        )
    else:
        effective_start_expr = (
            f"GREATEST(CAST({start_ms} AS BIGINT), bounds.first_observed_ms)"
        )
        effective_end_expr = f"CAST({end_ms} AS BIGINT)"

    con.execute(
        f"""
        CREATE OR REPLACE VIEW stage1_market_windows AS
        SELECT
            bounds.market_id,
            bounds.symbol,
            bounds.first_observed_ms,
            bounds.last_observed_ms,
            {effective_start_expr} AS effective_start_ms,
            {effective_end_expr} AS effective_end_ms,
            CAST({effective_end_expr} - {effective_start_expr} AS DOUBLE) / 1000.0
                AS market_observation_seconds,
            CAST(
                GREATEST(
                    1,
                    CAST(
                        ({effective_end_expr} - {effective_start_expr}) / {interval_ms}
                        AS DOUBLE
                    ) + 1
                ) AS BIGINT
            ) AS expected_samples
        FROM stage1_book_bounds bounds
        LEFT JOIN market_lifecycle_tbl lc ON lc.market_id = bounds.market_id
        """
    )


def _prepare_stage1_book_views(con: duckdb.DuckDBPyConnection) -> None:
    """Lightweight dedup + usability views aligned with Full Analyzer semantics."""
    con.execute(
        """
        CREATE OR REPLACE VIEW stage1_book_deduped AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY market_id, timestamp_ms ORDER BY timestamp_ms
            ) AS rn
            FROM stage1_book_raw
        ) WHERE rn = 1
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW stage1_book_observed AS
        SELECT * FROM stage1_book_deduped
        WHERE {BOOK_OBSERVED_PREDICATE}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW stage1_book_metrics_good AS
        SELECT * FROM stage1_book_deduped
        WHERE {BOOK_METRICS_GOOD_PREDICATE}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW stage1_spread_observed AS
        SELECT
            *,
            {EFFECTIVE_SPREAD_EXPR} AS effective_spread_bps
        FROM stage1_book_observed
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW stage1_book_bounds AS
        SELECT
            market_id,
            MAX(symbol) AS symbol,
            COUNT(*) AS book_observation_count,
            MIN(timestamp_ms) AS first_observed_ms,
            MAX(timestamp_ms) AS last_observed_ms
        FROM stage1_book_deduped
        GROUP BY market_id
        """
    )


def run_stage1(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    start_ms: int,
    end_ms: int,
    book_valid: list[str],
    trade_valid: list[str],
    markout_valid: list[str] | None = None,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None = None,
) -> list[Stage1Market]:
    """Lightweight per-market aggregation over all markets (no heavy materialization)."""
    if not book_valid:
        return []

    try:
        available_cols = _probe_parquet_columns(con, file_paths=book_valid)
    except Exception as exc:  # noqa: BLE001
        log.warning("stage1 book column probe failed; isolating: %s", exc)
        book_valid, _ = _isolate_readable_parquet_files(con, book_valid)
        if not book_valid:
            return []
        available_cols = _probe_parquet_columns(con, file_paths=book_valid)

    book_cols = _book_projection(available_cols)
    if not _read_view(
        con,
        "stage1_book_raw",
        None,
        book_cols,
        start_ms,
        end_ms,
        file_paths=book_valid,
    ):
        return []

    interval_ms = int(settings.book_sample_interval_seconds * 1000)
    _prepare_stage1_book_views(con)

    _create_stage1_market_windows_view(
        con,
        start_ms=start_ms,
        end_ms=end_ms,
        market_lifecycle=market_lifecycle,
        interval_ms=interval_ms,
    )

    persistence = _spread_persistence_from_relation(
        con,
        "stage1_spread_observed",
        settings.spread_thresholds_bps,
        settings.book_sample_interval_seconds,
    )

    book_sql = """
    SELECT
        mw.market_id,
        COALESCE(b.symbol, mw.symbol) AS symbol,
        COALESCE(c.observed_samples, 0) AS book_observation_count,
        COALESCE(c.observed_samples, 0) AS book_update_count,
        b.first_observed_ms AS book_first_seen_at,
        b.last_observed_ms AS book_last_seen_at,
        s.mean_spread_bps,
        s.median_spread_bps,
        s.p25_spread_bps,
        s.p75_spread_bps,
        d.median_two_sided_depth_10bps_usd,
        LEAST(
            100.0,
            100.0 * COALESCE(c.observed_samples, 0) / GREATEST(1, mw.expected_samples)
        ) AS observation_coverage_pct,
        LEAST(
            100.0,
            100.0 * COALESCE(c.observed_samples, 0) / GREATEST(1, mw.expected_samples)
        ) AS data_coverage_pct,
        mw.market_observation_seconds / 3600.0 AS observation_hours
    FROM stage1_market_windows mw
    LEFT JOIN stage1_book_bounds b ON b.market_id = mw.market_id
    LEFT JOIN (
        SELECT
            market_id,
            COUNT(*) AS observed_samples
        FROM stage1_book_deduped
        GROUP BY market_id
    ) c ON c.market_id = mw.market_id
    LEFT JOIN (
        SELECT
            market_id,
            AVG(effective_spread_bps) AS mean_spread_bps,
            quantile_cont(effective_spread_bps, 0.5) AS median_spread_bps,
            quantile_cont(effective_spread_bps, 0.25) AS p25_spread_bps,
            quantile_cont(effective_spread_bps, 0.75) AS p75_spread_bps
        FROM stage1_spread_observed
        WHERE effective_spread_bps IS NOT NULL
        GROUP BY market_id
    ) s ON s.market_id = mw.market_id
    LEFT JOIN (
        SELECT
            market_id,
            quantile_cont(two_sided_depth_10bps_usd, 0.5) AS median_two_sided_depth_10bps_usd
        FROM stage1_book_metrics_good
        WHERE two_sided_depth_10bps_usd IS NOT NULL
        GROUP BY market_id
    ) d ON d.market_id = mw.market_id
    """

    book_rows = {int(r[0]): r for r in con.execute(book_sql).fetchall()}

    trade_rows: dict[int, tuple[Any, ...]] = {}
    if trade_valid:
        try:
            trade_available = _probe_parquet_columns(con, file_paths=trade_valid)
        except Exception:  # noqa: BLE001
            trade_available = set()
        price_col = (
            "price" if "price" in trade_available else "CAST(NULL AS DOUBLE) AS price"
        )
        trade_cols = (
            "timestamp_ms, market_id, symbol, trade_id, usd_amount, type, "
            f"{price_col}"
        )
        if _read_view(
            con,
            "stage1_trade_raw",
            None,
            trade_cols,
            start_ms,
            end_ms,
            file_paths=trade_valid,
        ):
            con.execute(
                """
                CREATE OR REPLACE VIEW stage1_trade_deduped AS
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY market_id, trade_id ORDER BY timestamp_ms
                    ) AS rn
                    FROM stage1_trade_raw
                    WHERE type = 'trade'
                ) WHERE rn = 1
                """
            )
            trade_count_sql = """
            SELECT
                market_id,
                MAX(symbol) AS symbol,
                COUNT(*) AS trade_count,
                SUM(usd_amount) AS trade_volume,
                MIN(timestamp_ms) AS trade_first_seen_at,
                MAX(timestamp_ms) AS trade_last_seen_at
            FROM stage1_trade_deduped
            GROUP BY market_id
            """
            tpm_sql = """
            WITH per_minute AS (
                SELECT
                    market_id,
                    (timestamp_ms // 60000) AS minute_idx,
                    COUNT(*)::DOUBLE AS cnt
                FROM stage1_trade_deduped
                GROUP BY market_id, minute_idx
            )
            SELECT
                market_id,
                AVG(cnt) AS trades_per_minute_mean,
                quantile_cont(cnt, 0.5) AS trades_per_minute_median
            FROM per_minute
            GROUP BY market_id
            """
            tpm_by_id = {
                int(r[0]): (r[1], r[2])
                for r in con.execute(tpm_sql).fetchall()
            }
            for row in con.execute(trade_count_sql).fetchall():
                mid = int(row[0])
                tpm = tpm_by_id.get(mid)
                trade_rows[mid] = row + (
                    tpm[0] if tpm else None,
                    tpm[1] if tpm else None,
                )

    markout_by_id: dict[int, dict[str, Any]] = {}
    if markout_valid:
        markout_cols = (
            "timestamp_ms, market_id, symbol, trade_id, horizon_s, maker_markout_bps"
        )
        if _read_view(
            con,
            "stage1_markout_raw_view",
            None,
            markout_cols,
            start_ms,
            end_ms,
            file_paths=markout_valid,
        ):
            con.execute(
                "CREATE OR REPLACE TABLE markout_raw AS "
                "SELECT * FROM stage1_markout_raw_view"
            )
            markout_by_id = _aggregate_markouts_sql(con)
            try:
                con.execute("DROP TABLE IF EXISTS markout_raw")
            except Exception:  # noqa: BLE001
                pass

    all_ids = set(book_rows.keys()) | set(trade_rows.keys()) | set(markout_by_id.keys())
    markets: list[Stage1Market] = []

    for mid in sorted(all_ids):
        br = book_rows.get(mid)
        tr = trade_rows.get(mid)
        mo = markout_by_id.get(mid, {})

        if br:
            (
                _mid,
                symbol,
                book_obs,
                book_upd,
                book_first,
                book_last,
                mean_spread,
                median_spread,
                p25_spread,
                p75_spread,
                depth10,
                obs_cov_pct,
                data_cov_pct,
                obs_hours,
            ) = br
        else:
            symbol = tr[1] if tr else markout_by_id.get(mid, {}).get("symbol")
            book_obs = 0
            book_upd = 0
            book_first = None
            book_last = None
            mean_spread = None
            median_spread = None
            p25_spread = None
            p75_spread = None
            depth10 = None
            obs_cov_pct = 0.0
            data_cov_pct = 0.0
            obs_hours = 0.0

        trade_count = int(tr[2] or 0) if tr else 0
        trade_volume = float(tr[3] or 0.0) if tr else 0.0
        trade_first = int(tr[4]) if tr and tr[4] is not None else None
        trade_last = int(tr[5]) if tr and tr[5] is not None else None
        tpm_mean = float(tr[6]) if tr and tr[6] is not None else None
        tpm_median = float(tr[7]) if tr and tr[7] is not None else None

        pers = persistence.get(mid, {})
        pct_ge5 = pers.get("pct_time_spread_ge_5bps")

        m = Stage1Market(
            market_id=mid,
            symbol=symbol,
            book_observation_count=int(book_obs or 0),
            book_update_count=int(book_upd or 0),
            trade_count=trade_count,
            trade_volume=trade_volume,
            observation_coverage=float(obs_cov_pct or 0.0) / 100.0,
            book_first_seen_at=int(book_first) if book_first is not None else None,
            book_last_seen_at=int(book_last) if book_last is not None else None,
            trade_first_seen_at=trade_first,
            trade_last_seen_at=trade_last,
            median_spread_bps=float(median_spread) if median_spread is not None else None,
            mean_spread_bps=float(mean_spread) if mean_spread is not None else None,
            p25_spread_bps=float(p25_spread) if p25_spread is not None else None,
            p75_spread_bps=float(p75_spread) if p75_spread is not None else None,
            median_two_sided_depth_10bps_usd=(
                float(depth10) if depth10 is not None else None
            ),
            pct_time_spread_ge_5bps=float(pct_ge5) if pct_ge5 is not None else None,
            trades_per_minute_mean=tpm_mean,
            trades_per_minute_median=tpm_median,
            maker_markout_5s_median_bps=mo.get("maker_markout_5s_median_bps"),
            observation_hours=float(obs_hours or 0.0),
            observation_coverage_pct=float(obs_cov_pct or 0.0),
            data_coverage_pct=float(data_cov_pct or 0.0),
        )
        markets.append(m)

    _apply_eligibility(markets, settings)
    _apply_screening_scores(markets)
    return markets


def _apply_eligibility(markets: list[Stage1Market], settings: Settings) -> None:
    min_cov = settings.analyzer_stage1_min_coverage
    min_trades = settings.analyzer_stage1_min_trades
    min_spread = settings.analyzer_stage1_min_spread_bps
    for m in markets:
        m.eligible = (
            m.observation_coverage >= min_cov
            and m.trade_count >= min_trades
            and m.median_spread_bps is not None
            and m.median_spread_bps >= min_spread
        )


def _apply_screening_scores(markets: list[Stage1Market]) -> None:
    """Rank-aggregation screening score (not for dashboard profit display)."""
    spreads = [m.median_spread_bps for m in markets if m.median_spread_bps is not None]
    activities = [float(m.trade_count) for m in markets]
    coverages = [m.observation_coverage for m in markets]

    for m in markets:
        if not m.eligible:
            m.screening_score = None
            continue
        spread_rank = _pct_rank(spreads, m.median_spread_bps) if spreads else 0.0
        activity_rank = _pct_rank(activities, float(m.trade_count)) if activities else 0.0
        coverage_rank = _pct_rank(coverages, m.observation_coverage) if coverages else 0.0
        m.screening_score = (spread_rank + activity_rank + coverage_rank) / 300.0


def select_stage2_markets(
    stage1: list[Stage1Market],
    *,
    top_n: int,
    extra_market_ids: set[int] | None = None,
) -> list[int]:
    """Return market_ids for Stage 2 full analysis."""
    eligible = [m for m in stage1 if m.eligible]
    if not eligible:
        base: list[int] = []
    else:
        eligible.sort(
            key=lambda m: (m.screening_score is not None, m.screening_score or 0.0),
            reverse=True,
        )
        if len(eligible) <= top_n:
            base = [m.market_id for m in eligible]
        else:
            base = [m.market_id for m in eligible[:top_n]]

    if extra_market_ids:
        return sorted(set(base) | extra_market_ids)
    return base


def stage1_to_peer_row(m: Stage1Market, hours: float) -> dict[str, Any]:
    """Lightweight peer row for cross-sectional score percentiles (all markets)."""
    return {
        "market_id": m.market_id,
        "symbol": m.symbol,
        "median_spread_bps": m.median_spread_bps,
        "median_two_sided_depth_10bps_usd": m.median_two_sided_depth_10bps_usd,
        "pct_time_spread_ge_5bps": m.pct_time_spread_ge_5bps,
        "trades_per_minute_median": m.trades_per_minute_median,
        "trades_per_minute_mean": m.trades_per_minute_mean,
        "total_trade_count": m.trade_count,
        "maker_markout_5s_median_bps": m.maker_markout_5s_median_bps,
        "observation_coverage_pct": m.observation_coverage_pct,
        "data_coverage_pct": m.data_coverage_pct,
        "observation_hours": m.observation_hours or hours,
        "analysis_window_hours": hours,
        "analysis_scope": "rolling",
    }


def stage1_to_dict(m: Stage1Market) -> dict[str, Any]:
    return {
        "eligible": m.eligible,
        "screening_score": m.screening_score,
        "observation_coverage": m.observation_coverage,
        "trade_count": m.trade_count,
        "median_spread_bps": m.median_spread_bps,
        "book_observation_count": m.book_observation_count,
        "book_update_count": m.book_update_count,
        "trade_volume": m.trade_volume,
        "mean_spread_bps": m.mean_spread_bps,
        "p25_spread_bps": m.p25_spread_bps,
        "p75_spread_bps": m.p75_spread_bps,
    }


def screened_market_row(m: Stage1Market, hours: float) -> dict[str, Any]:
    """Dashboard row for Stage-2-skipped markets (null semantics for unanalyzed fields)."""
    return {
        "symbol": m.symbol,
        "market_id": m.market_id,
        "analysis_stage": "screened",
        "stage1": stage1_to_dict(m),
        "score": None,
        "letter_rank": None,
        "is_candidate": False,
        "median_spread_bps": m.median_spread_bps,
        "pct_time_spread_ge_5bps": m.pct_time_spread_ge_5bps,
        "median_two_sided_depth_10bps_usd": m.median_two_sided_depth_10bps_usd,
        "trades_per_minute_median": m.trades_per_minute_median,
        "trades_per_minute_mean": m.trades_per_minute_mean,
        "total_trade_count": m.trade_count,
        "markout_5s_count": None,
        "markout_30s_count": None,
        "markout_sample_quality": None,
        "maker_markout_5s_median_bps": None,
        "maker_markout_30s_median_bps": None,
        "estimated_maker_fill_rate_5s_conservative": None,
        "estimated_maker_fill_rate_30s_conservative": None,
        "estimated_maker_fill_rate_5s_optimistic": None,
        "estimated_maker_fill_rate_30s_optimistic": None,
        "estimated_maker_fill_samples": None,
        "estimated_maker_fill_sample_quality": None,
        "estimated_maker_edge_5s_bps": None,
        "estimated_maker_edge_30s_bps": None,
        "estimated_maker_edge_fee_included": None,
        "analysis_scope": "rolling",
        "analysis_window_hours": hours,
        "current_funding_rate": None,
        "data_coverage_pct": m.data_coverage_pct,
        "observation_coverage_pct": m.observation_coverage_pct,
        "usable_quote_coverage_pct": None,
        "spread_coverage_pct": None,
        "observed_samples": m.book_observation_count,
        "expected_samples": None,
        "observation_hours": m.observation_hours,
        "latest_book_update_age_seconds": None,
        "p95_book_update_age_seconds": None,
        "recommended_max_order_usd": None,
        "warnings": [],
        "pros": [],
        "cons": [],
    }


def merge_screening_and_full_results(
    stage1: list[Stage1Market],
    full_results: dict[str, Any],
    *,
    hours: float,
    start_ms: int,
    end_ms: int,
    stage1_elapsed: float,
    stage2_elapsed: float,
    selected_market_ids: list[int],
) -> dict[str, Any]:
    """Combine Stage 1 screening with Stage 2 full analysis output."""
    selected_set = frozenset(selected_market_ids)
    full_scored = full_results.get("scored") or []
    scored_by_id = {int(s.row.get("market_id")): s for s in full_scored}

    screened: list[dict[str, Any]] = []
    all_market_rows: list[dict[str, Any]] = []
    scored: list[Any] = []

    for m in stage1:
        if m.market_id in selected_set and m.market_id in scored_by_id:
            s = scored_by_id[m.market_id]
            row = dict(s.row)
            row["analysis_stage"] = "full"
            row["stage1"] = stage1_to_dict(m)
            s.row.update({"analysis_stage": "full", "stage1": stage1_to_dict(m)})
            scored.append(s)
            all_market_rows.append(row)
        else:
            screened.append(screened_market_row(m, hours))
            all_market_rows.append(screened[-1])

    eligible_count = sum(1 for m in stage1 if m.eligible)
    total_elapsed = stage1_elapsed + stage2_elapsed

    result: dict[str, Any] = {
        "hours": hours,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "markets": all_market_rows,
        "scored": scored,
        "screened": screened,
        "candidates": [s for s in scored if s.candidate],
        "avoid": full_results.get("avoid") or [],
        "book_row_count": full_results.get("book_row_count", 0),
        "latest_book_event_ms": full_results.get("latest_book_event_ms"),
        "trade_row_count": full_results.get("trade_row_count", 0),
        "markout_row_count": full_results.get("markout_row_count", 0),
        "parquet_health": full_results.get("parquet_health"),
        "error": full_results.get("error"),
        "two_stage": {
            "stage1_elapsed_seconds": stage1_elapsed,
            "stage2_elapsed_seconds": stage2_elapsed,
            "total_elapsed_seconds": total_elapsed,
            "markets_total": len(stage1),
            "markets_eligible": eligible_count,
            "markets_selected": len(selected_market_ids),
            "markets_full_analyzed": len(scored),
            "stage2_selection_ratio": (
                len(selected_market_ids) / len(stage1) if stage1 else 0.0
            ),
            "stage2_book_row_count": full_results.get("book_row_count", 0),
            "stage2_trade_row_count": full_results.get("trade_row_count", 0),
            "stage2_markout_row_count": full_results.get("markout_row_count", 0),
        },
    }
    if full_results.get("benchmark_profile"):
        result["benchmark_profile"] = full_results["benchmark_profile"]
    if full_results.get("volatility_explain"):
        result["volatility_explain"] = full_results["volatility_explain"]
    return result


def log_stage1_complete(
    total: int,
    eligible: int,
    selected: int,
    elapsed: float,
) -> None:
    log.info(
        "[Analyzer] Stage 1 completed: total=%s eligible=%s selected=%s elapsed=%.3fs",
        total,
        eligible,
        selected,
        elapsed,
    )


def log_stage2_started(markets: int) -> None:
    log.info("[Analyzer] Stage 2 started: markets=%s", markets)


def log_stage2_completed(markets: int, elapsed: float) -> None:
    log.info("[Analyzer] Stage 2 completed: markets=%s elapsed=%.3fs", markets, elapsed)


def log_two_stage_completed(stage1_elapsed: float, stage2_elapsed: float) -> None:
    log.info(
        "[Analyzer] completed: total_elapsed=%.3fs",
        stage1_elapsed + stage2_elapsed,
    )
