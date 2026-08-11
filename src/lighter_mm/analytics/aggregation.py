"""DuckDB SQL aggregation over collected Parquet datasets (memory-safe for 72h)."""

from __future__ import annotations

import logging
import resource
import sys
import time
from typing import Any

import duckdb

from lighter_mm.analytics.book_metrics import (
    _aggregate_book_sql,
    _spread_persistence,
    _spread_persistence_sql,
    _volatility_explain_plans,
    _volatility_sql,
)
from lighter_mm.analytics.estimated_fill_metrics import (
    aggregate_estimated_fill_sql,
    attach_estimated_maker_edge,
    empty_estimated_fill_stats,
    estimated_fill_explain_plans,
    sample_quality,
)
from lighter_mm.analytics.markout_metrics import (
    _aggregate_markouts_sql,
    _empty_markout_stats,
    _markout_stats,
)
from lighter_mm.analytics.parquet_source import (
    AnalysisSources,
    _book_projection,
    _connect,
    _default_sources,
    _glob_or_none,
    _glob_patterns,
    _isolate_readable_parquet_files,
    _probe_parquet_columns,
    _read_view,
)
from lighter_mm.analytics.screening import (
    log_stage1_complete,
    log_stage2_completed,
    log_stage2_started,
    log_two_stage_completed,
    merge_screening_and_full_results,
    run_stage1,
    select_stage2_markets,
    stage1_to_peer_row,
)
from lighter_mm.analytics.trade_metrics import (
    _aggregate_trades_sql,
    _empty_trade_stats,
    _trade_stats,
)
from lighter_mm.config import Settings
from lighter_mm.paper_mm import run_paper_mm_for_scored
from lighter_mm.scoring import (
    CandidateThresholds,
    ScoredMarket,
    avoid_wide_spread_markets,
    score_markets,
)
from lighter_mm.storage.parquet_validation import (
    parquet_health_summary,
    prepare_parquet_dataset,
)
from lighter_mm.storage.state import MarketLifecycleEntry

log = logging.getLogger(__name__)

# Re-export for backward-compatible imports.
__all__ = [
    "AnalysisSources",
    "analyze_range",
    "analyze_window",
    "scored_to_records",
    "_glob_or_none",
    "_glob_patterns",
    "_spread_persistence",
    "_trade_stats",
    "_markout_stats",
]


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def _register_market_lifecycle_table(
    con: duckdb.DuckDBPyConnection,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None,
) -> bool:
    """Register lifecycle bounds. Returns True when lifecycle metadata is provided."""
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


def _create_market_windows_view(
    con: duckdb.DuckDBPyConnection,
    *,
    start_ms: int,
    end_ms: int,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None = None,
) -> None:
    """Lifecycle-aware per-market observation bounds for coverage metrics.

    With ``market_lifecycle`` (Cloud Analyzer / RunState):
    - ``effective_start_ms = max(analysis_start_ms, first_active_at_ms)``
    - active at analysis end → ``effective_end_ms = end_ms``
    - removed before analysis end → ``effective_end_ms = min(end_ms, removed_at_ms)``

    Parquet first/last observed timestamps are diagnostic only; they do not
    define the lifecycle window. Inactive time is never inferred from
    ``last_observed_ms``.

    Fallback when ``market_lifecycle`` is None (local analysis without RunState):
    treat markets as active through ``end_ms`` so trailing collector outages
    are not hidden. Start uses the first observed Parquet row within the window.
    """
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
        CREATE OR REPLACE VIEW market_windows AS
        SELECT
            bounds.market_id,
            bounds.symbol,
            bounds.first_observed_ms,
            bounds.last_observed_ms,
            {effective_start_expr} AS effective_start_ms,
            {effective_end_expr} AS effective_end_ms,
            CAST({effective_end_expr} - {effective_start_expr} AS DOUBLE) / 1000.0
                AS market_observation_seconds
        FROM (
            SELECT
                market_id,
                MAX(symbol) AS symbol,
                MIN(timestamp_ms) AS first_observed_ms,
                MAX(timestamp_ms) AS last_observed_ms
            FROM book_deduped
            GROUP BY market_id
        ) bounds
        LEFT JOIN market_lifecycle_tbl lc ON lc.market_id = bounds.market_id
        """
    )


def _prepare_parquet_sources(
    src: AnalysisSources,
    *,
    read_only: bool,
    t0: float,
) -> tuple[list[str], list[str], list[str], list[dict[str, str]], dict[str, Any]]:
    prep_kwargs = {
        "quarantine": not read_only,
        "cleanup_temp": not read_only,
    }
    book_valid, book_corrupt = prepare_parquet_dataset(src.books, **prep_kwargs)
    trade_valid, trade_corrupt = prepare_parquet_dataset(src.trades, **prep_kwargs)
    markout_valid, markout_corrupt = prepare_parquet_dataset(src.markouts, **prep_kwargs)
    all_corrupt = book_corrupt + trade_corrupt + markout_corrupt
    parquet_health = parquet_health_summary(
        {
            "books": len(book_valid),
            "trades": len(trade_valid),
            "markouts": len(markout_valid),
        },
        all_corrupt,
    )
    log.info(
        "analysis phase=parquet_prepare elapsed=%.3fs valid_books=%s valid_trades=%s "
        "valid_markouts=%s corrupt_files=%s rss_mb=%.1f",
        time.monotonic() - t0,
        len(book_valid),
        len(trade_valid),
        len(markout_valid),
        len(all_corrupt),
        _rss_mb(),
    )
    return book_valid, trade_valid, markout_valid, all_corrupt, parquet_health


def _probe_book_columns(
    con: duckdb.DuckDBPyConnection,
    book_valid: list[str],
    trade_valid: list[str],
    markout_valid: list[str],
    all_corrupt: list[dict[str, str]],
) -> tuple[list[str], set[str], dict[str, Any]] | dict[str, Any]:
    """Probe book columns; may isolate corrupt files.

    Returns either ``(book_valid, available_cols, parquet_health)`` or an
    early-error result dict.
    """
    try:
        available_cols = _probe_parquet_columns(con, file_paths=book_valid)
    except Exception as exc:  # noqa: BLE001
        log.warning("book parquet batch probe failed; isolating bad files: %s", exc)
        book_valid, read_bad = _isolate_readable_parquet_files(con, book_valid)
        all_corrupt.extend(read_bad)
        parquet_health = parquet_health_summary(
            {
                "books": len(book_valid),
                "trades": len(trade_valid),
                "markouts": len(markout_valid),
            },
            all_corrupt,
        )
        if not book_valid:
            log.error(
                "analysis failed reason=no_readable_parquet_files corrupt_files=%s",
                len(all_corrupt),
            )
            return {
                "error": "no readable book_samples parquet files",
                "parquet_health": parquet_health,
            }
        try:
            available_cols = _probe_parquet_columns(con, file_paths=book_valid)
        except Exception as retry_exc:  # noqa: BLE001
            log.exception("book parquet column probe failed after isolation: %s", retry_exc)
            return {
                "error": f"book_samples parquet probe failed: {retry_exc}",
                "parquet_health": parquet_health,
            }
    else:
        parquet_health = parquet_health_summary(
            {
                "books": len(book_valid),
                "trades": len(trade_valid),
                "markouts": len(markout_valid),
            },
            all_corrupt,
        )
    return book_valid, available_cols, parquet_health


def _prepare_book_dataset(
    con: duckdb.DuckDBPyConnection,
    *,
    book_valid: list[str],
    available_cols: set[str],
    start_ms: int,
    end_ms: int,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None,
    t0: float,
    benchmark_profile: bool,
    profile: dict[str, float],
    market_ids: frozenset[int] | None = None,
) -> tuple[int, int | None] | None:
    """Materialize book tables. Returns (book_count, latest_book_event_ms) or None."""
    book_cols = _book_projection(available_cols)
    if not _read_view(
        con,
        "book_raw",
        None,
        book_cols,
        start_ms,
        end_ms,
        file_paths=book_valid,
        market_ids=market_ids,
    ):
        return None

    # Materialize projected/deduped books once onto local DuckDB storage.
    # Re-scanning hive Parquet via GCS FUSE for every aggregate CTE caused the
    # Cloud Run Analyzer to OOM at ~4Gi after book_load (~1.2Gi RSS / ~2.7M rows).
    con.execute(
        """
        CREATE OR REPLACE TABLE book_deduped AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY market_id, timestamp_ms ORDER BY timestamp_ms
            ) AS rn
            FROM book_raw
        ) WHERE rn = 1
        """
    )
    try:
        con.execute("DROP VIEW IF EXISTS book_raw")
    except Exception:  # noqa: BLE001
        pass
    con.execute(
        """
        CREATE OR REPLACE TABLE book_observed AS
        SELECT * FROM book_deduped
        WHERE is_usable = true
           OR (is_usable IS NULL AND mid IS NOT NULL)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE book_spread_observed AS
        SELECT
            *,
            CASE
                WHEN spread_bps IS NOT NULL THEN spread_bps
                WHEN best_bid IS NOT NULL AND best_ask IS NOT NULL AND mid IS NOT NULL
                     AND mid > 0 AND best_ask >= best_bid
                THEN (best_ask - best_bid) / mid * 10000.0
                ELSE NULL
            END AS effective_spread_bps
        FROM book_observed
        """
    )
    # Spread/depth metrics: new rows via is_usable; legacy rows exclude stale-only
    # depth zeros (is_stale=true with no is_usable column). Legacy spread may be
    # recovered from best_bid/best_ask/mid when spread_bps was nulled by old collector.
    con.execute(
        """
        CREATE OR REPLACE TABLE book_metrics_good AS
        SELECT
            *,
            CASE
                WHEN spread_bps IS NOT NULL THEN spread_bps
                WHEN best_bid IS NOT NULL AND best_ask IS NOT NULL AND mid IS NOT NULL
                     AND mid > 0 AND best_ask >= best_bid
                THEN (best_ask - best_bid) / mid * 10000.0
                ELSE NULL
            END AS effective_spread_bps
        FROM book_deduped
        WHERE is_usable = true
           OR (is_usable IS NULL AND is_stale = false AND mid IS NOT NULL)
        """
    )
    _create_market_windows_view(
        con,
        start_ms=start_ms,
        end_ms=end_ms,
        market_lifecycle=market_lifecycle,
    )

    book_count = con.execute("SELECT COUNT(*) FROM book_deduped").fetchone()[0]
    latest_book_event_ms = con.execute("SELECT MAX(timestamp_ms) FROM book_deduped").fetchone()[0]
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_book_load_mb"] = rss
    log.info(
        "analysis phase=book_load elapsed=%.3fs book_rows=%s rss_mb=%.1f",
        time.monotonic() - t0,
        book_count,
        rss,
    )
    return int(book_count), latest_book_event_ms


def _run_book_metrics(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    start_ms: int,
    end_ms: int,
    hours: float,
    benchmark_profile: bool,
    profile: dict[str, float],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    t_book = time.monotonic()
    book_agg = _aggregate_book_sql(con, settings, start_ms, end_ms, hours)
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_book_aggregate_mb"] = rss
    log.info(
        "analysis phase=book_aggregate elapsed=%.3fs markets=%s rss_mb=%.1f",
        time.monotonic() - t_book,
        len(book_agg),
        rss,
    )

    t_persist = time.monotonic()
    persistence = _spread_persistence_sql(
        con, settings.spread_thresholds_bps, settings.book_sample_interval_seconds
    )
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_spread_persistence_mb"] = rss
    log.info(
        "analysis phase=spread_persistence elapsed=%.3fs rss_mb=%.1f",
        time.monotonic() - t_persist,
        rss,
    )

    t_vol = time.monotonic()
    # Thin mid series for horizon joins — avoids re-scanning wide book_observed rows.
    con.execute(
        """
        CREATE OR REPLACE TABLE book_mids AS
        SELECT market_id, timestamp_ms, mid
        FROM book_observed
        WHERE mid IS NOT NULL AND mid > 0
        """
    )
    log.info(
        "analysis phase=volatility_prepare elapsed=%.3fs rss_mb=%.1f",
        time.monotonic() - t_vol,
        _rss_mb(),
    )
    volatility = _volatility_sql(con, settings)
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_volatility_mb"] = rss
    log.info(
        "analysis phase=volatility elapsed=%.3fs rss_mb=%.1f",
        time.monotonic() - t_vol,
        rss,
    )
    return book_agg, persistence, volatility


def _prepare_trade_dataset(
    con: duckdb.DuckDBPyConnection,
    *,
    trade_valid: list[str],
    start_ms: int,
    end_ms: int,
    market_ids: frozenset[int] | None = None,
) -> tuple[int, set[str]] | None:
    """Load and materialize trades. Returns (trade_count, available_cols) or None."""
    try:
        trade_available = _probe_parquet_columns(con, file_paths=trade_valid)
    except Exception as exc:  # noqa: BLE001
        log.warning("trade parquet column probe failed: %s", exc)
        trade_available = set()
    price_col = (
        "price" if "price" in trade_available else "CAST(NULL AS DOUBLE) AS price"
    )
    is_maker_ask_col = (
        "is_maker_ask"
        if "is_maker_ask" in trade_available
        else "CAST(NULL AS BOOLEAN) AS is_maker_ask"
    )
    trade_cols = (
        "timestamp_ms, market_id, symbol, trade_id, usd_amount, type, "
        f"{price_col}, {is_maker_ask_col}"
    )
    if not _read_view(
        con,
        "trade_raw_view",
        None,
        trade_cols,
        start_ms,
        end_ms,
        file_paths=trade_valid,
        market_ids=market_ids,
    ):
        return None
    con.execute("CREATE OR REPLACE TABLE trade_raw AS SELECT * FROM trade_raw_view")
    try:
        con.execute("DROP VIEW IF EXISTS trade_raw_view")
    except Exception:  # noqa: BLE001
        pass
    trade_count = con.execute("SELECT COUNT(*) FROM trade_raw").fetchone()[0]
    return int(trade_count), trade_available


def _run_trade_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    trade_available: set[str],
    benchmark_profile: bool,
    profile: dict[str, float],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    t_trade = time.monotonic()
    trade_agg = _aggregate_trades_sql(con)
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_trade_aggregate_mb"] = rss
    log.info(
        "analysis phase=trade_aggregate elapsed=%.3fs trade_rows=%s rss_mb=%.1f",
        time.monotonic() - t_trade,
        con.execute("SELECT COUNT(*) FROM trade_raw").fetchone()[0],
        rss,
    )
    # Required source fields must exist in Parquet — not merely as
    # all-NULL projected placeholders — or fill would look like 0%.
    fill_source_ok = "price" in trade_available and "is_maker_ask" in trade_available
    t_fill = time.monotonic()
    estimated_fill_agg = aggregate_estimated_fill_sql(
        con, source_fields_available=fill_source_ok
    )
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_estimated_fill_mb"] = rss
    log.info(
        "analysis phase=estimated_fill elapsed=%.3fs markets=%s "
        "source_fields_ok=%s rss_mb=%.1f",
        time.monotonic() - t_fill,
        len(estimated_fill_agg),
        fill_source_ok,
        rss,
    )
    return trade_agg, estimated_fill_agg


def _prepare_markout_dataset(
    con: duckdb.DuckDBPyConnection,
    *,
    markout_valid: list[str],
    start_ms: int,
    end_ms: int,
    market_ids: frozenset[int] | None = None,
) -> int | None:
    """Load and materialize markouts. Returns markout_count or None."""
    markout_cols = (
        "timestamp_ms, market_id, symbol, trade_id, horizon_s, maker_markout_bps"
    )
    if not _read_view(
        con,
        "markout_raw_view",
        None,
        markout_cols,
        start_ms,
        end_ms,
        file_paths=markout_valid,
        market_ids=market_ids,
    ):
        return None
    con.execute(
        "CREATE OR REPLACE TABLE markout_raw AS SELECT * FROM markout_raw_view"
    )
    try:
        con.execute("DROP VIEW IF EXISTS markout_raw_view")
    except Exception:  # noqa: BLE001
        pass
    return int(con.execute("SELECT COUNT(*) FROM markout_raw").fetchone()[0])


def _run_markout_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    markout_count: int,
    benchmark_profile: bool,
    profile: dict[str, float],
) -> dict[int, dict[str, Any]]:
    t_markout = time.monotonic()
    markout_agg = _aggregate_markouts_sql(con)
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_markout_aggregate_mb"] = rss
    log.info(
        "analysis phase=markout_aggregate elapsed=%.3fs markout_rows=%s rss_mb=%.1f",
        time.monotonic() - t_markout,
        markout_count,
        rss,
    )
    return markout_agg


def _enrich_peer_fill_only(
    peer_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add Estimated Maker Fill to peer rows for Stage 2 analyzed markets only."""
    full_by_id = {int(r["market_id"]): r for r in full_rows}
    enriched: list[dict[str, Any]] = []
    for peer in peer_rows:
        row = dict(peer)
        mid = int(row["market_id"])
        if mid in full_by_id:
            fr = full_by_id[mid]
            row["estimated_maker_fill_rate_30s_conservative"] = fr.get(
                "estimated_maker_fill_rate_30s_conservative"
            )
        enriched.append(row)
    return enriched


def _scoring_peer_rows(
    rows: list[dict[str, Any]],
    peer_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Choose peer population for cross-sectional score percentiles."""
    if peer_rows is None:
        return None
    row_ids = {int(r["market_id"]) for r in rows}
    peer_ids = {int(p["market_id"]) for p in peer_rows}
    if row_ids == peer_ids:
        # All peer markets received full analysis — use full rows for exact semantics.
        return None
    return _enrich_peer_fill_only(peer_rows, rows)


def _empty_full_results(
    *,
    hours: float,
    start_ms: int,
    end_ms: int,
    parquet_health: dict[str, Any],
) -> dict[str, Any]:
    return {
        "hours": hours,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "markets": [],
        "scored": [],
        "candidates": [],
        "avoid": [],
        "book_row_count": 0,
        "latest_book_event_ms": None,
        "trade_row_count": 0,
        "markout_row_count": 0,
        "parquet_health": parquet_health,
    }


def analyze_range(
    settings: Settings,
    *,
    start_ms: int,
    end_ms: int,
    sources: AnalysisSources | None = None,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None = None,
    benchmark_profile: bool = False,
    explain_volatility: bool = False,
    duckdb_memory_limit: str | None = None,
    duckdb_threads: int | None = None,
    read_only: bool = False,
    paper_mm_market_ids: set[int] | None = None,
    paper_mm_top_n_override: int | None = None,
    paper_mm_order_usd_override: float | None = None,
) -> dict[str, Any]:
    """Aggregate Parquet over an explicit time window and source paths."""
    if end_ms <= start_ms:
        return {
            "hours": 0.0,
            "markets": [],
            "scored": [],
            "error": "invalid analysis window (end_ms <= start_ms)",
        }

    hours = max((end_ms - start_ms) / 3_600_000.0, 0.001)

    if not settings.analyzer_two_stage_enabled:
        return _analyze_range_impl(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            hours=hours,
            sources=sources,
            market_lifecycle=market_lifecycle,
            benchmark_profile=benchmark_profile,
            explain_volatility=explain_volatility,
            duckdb_memory_limit=duckdb_memory_limit,
            duckdb_threads=duckdb_threads,
            read_only=read_only,
            market_ids=None,
            peer_rows=None,
            paper_mm_market_ids=paper_mm_market_ids,
            paper_mm_top_n_override=paper_mm_top_n_override,
            paper_mm_order_usd_override=paper_mm_order_usd_override,
        )

    # Two-stage: Stage 1 screening → Stage 2 full analysis on selected markets
    src = sources or _default_sources(settings.data_dir)
    t0 = time.monotonic()
    book_valid, trade_valid, markout_valid, all_corrupt, parquet_health = (
        _prepare_parquet_sources(src, read_only=read_only, t0=t0)
    )

    if not book_valid:
        log.error(
            "analysis failed reason=no_valid_parquet_files corrupt_files=%s",
            len(all_corrupt),
        )
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": (
                "no valid book_samples parquet files"
                if all_corrupt
                else "no book_samples yet"
            ),
            "parquet_health": parquet_health,
        }

    log.info("[Analyzer] Stage 1 started: markets=pending")
    stage1_con = _connect(
        settings.data_dir,
        memory_limit=duckdb_memory_limit,
        threads=duckdb_threads,
    )
    t_stage1 = time.monotonic()
    try:
        stage1_markets = run_stage1(
            stage1_con,
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            book_valid=book_valid,
            trade_valid=trade_valid,
            markout_valid=markout_valid,
            market_lifecycle=market_lifecycle,
        )
    finally:
        try:
            stage1_con.close()
        except Exception:  # noqa: BLE001
            pass
    stage1_elapsed = time.monotonic() - t_stage1

    selected = select_stage2_markets(
        stage1_markets,
        top_n=settings.analyzer_stage2_top_n,
        extra_market_ids=paper_mm_market_ids,
    )
    eligible_count = sum(1 for m in stage1_markets if m.eligible)
    log_stage1_complete(
        len(stage1_markets),
        eligible_count,
        len(selected),
        stage1_elapsed,
    )

    peer_rows = [stage1_to_peer_row(m, hours) for m in stage1_markets]

    log_stage2_started(len(selected))
    t_stage2 = time.monotonic()
    if selected:
        full_results = _analyze_range_impl(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            hours=hours,
            sources=src,
            market_lifecycle=market_lifecycle,
            benchmark_profile=benchmark_profile,
            explain_volatility=explain_volatility,
            duckdb_memory_limit=duckdb_memory_limit,
            duckdb_threads=duckdb_threads,
            read_only=read_only,
            market_ids=frozenset(selected),
            peer_rows=peer_rows,
            prevalidated=(
                book_valid,
                trade_valid,
                markout_valid,
                all_corrupt,
                parquet_health,
            ),
            paper_mm_market_ids=paper_mm_market_ids,
            paper_mm_top_n_override=paper_mm_top_n_override,
            paper_mm_order_usd_override=paper_mm_order_usd_override,
        )
    else:
        full_results = _empty_full_results(
            hours=hours,
            start_ms=start_ms,
            end_ms=end_ms,
            parquet_health=parquet_health,
        )
    stage2_elapsed = time.monotonic() - t_stage2
    log_stage2_completed(len(selected), stage2_elapsed)
    log_two_stage_completed(stage1_elapsed, stage2_elapsed)

    return merge_screening_and_full_results(
        stage1_markets,
        full_results,
        hours=hours,
        start_ms=start_ms,
        end_ms=end_ms,
        stage1_elapsed=stage1_elapsed,
        stage2_elapsed=stage2_elapsed,
        selected_market_ids=selected,
    )


def _analyze_range_impl(
    settings: Settings,
    *,
    start_ms: int,
    end_ms: int,
    hours: float,
    sources: AnalysisSources | None = None,
    market_lifecycle: dict[int, MarketLifecycleEntry] | None = None,
    benchmark_profile: bool = False,
    explain_volatility: bool = False,
    duckdb_memory_limit: str | None = None,
    duckdb_threads: int | None = None,
    read_only: bool = False,
    market_ids: frozenset[int] | None = None,
    peer_rows: list[dict[str, Any]] | None = None,
    prevalidated: tuple[
        list[str],
        list[str],
        list[str],
        list[dict[str, str]],
        dict[str, Any],
    ] | None = None,
    paper_mm_market_ids: set[int] | None = None,
    paper_mm_top_n_override: int | None = None,
    paper_mm_order_usd_override: float | None = None,
) -> dict[str, Any]:
    """Core analyzer pipeline (optionally filtered to ``market_ids``)."""
    src = sources or _default_sources(settings.data_dir)
    con = _connect(
        settings.data_dir,
        memory_limit=duckdb_memory_limit,
        threads=duckdb_threads,
    )
    t0 = time.monotonic()
    profile: dict[str, float] = {}

    if prevalidated is not None:
        book_valid, trade_valid, markout_valid, all_corrupt, parquet_health = (
            prevalidated
        )
    else:
        book_valid, trade_valid, markout_valid, all_corrupt, parquet_health = (
            _prepare_parquet_sources(src, read_only=read_only, t0=t0)
        )

    if not book_valid:
        log.error(
            "analysis failed reason=no_valid_parquet_files corrupt_files=%s",
            len(all_corrupt),
        )
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": (
                "no valid book_samples parquet files"
                if all_corrupt
                else "no book_samples yet"
            ),
            "parquet_health": parquet_health,
        }

    probed = _probe_book_columns(
        con, book_valid, trade_valid, markout_valid, all_corrupt
    )
    if isinstance(probed, dict):
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": probed["error"],
            "parquet_health": probed["parquet_health"],
        }
    book_valid, available_cols, parquet_health = probed

    book_loaded = _prepare_book_dataset(
        con,
        book_valid=book_valid,
        available_cols=available_cols,
        start_ms=start_ms,
        end_ms=end_ms,
        market_lifecycle=market_lifecycle,
        t0=t0,
        benchmark_profile=benchmark_profile,
        profile=profile,
        market_ids=market_ids,
    )
    if book_loaded is None:
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": "no book_samples yet",
            "parquet_health": parquet_health,
        }
    book_count, latest_book_event_ms = book_loaded

    book_agg, persistence, volatility = _run_book_metrics(
        con,
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        hours=hours,
        benchmark_profile=benchmark_profile,
        profile=profile,
    )

    trade_count = 0
    trade_agg: dict[int, dict[str, Any]] = {}
    estimated_fill_agg: dict[int, dict[str, Any]] = {}
    if trade_valid:
        trade_loaded = _prepare_trade_dataset(
            con,
            trade_valid=trade_valid,
            start_ms=start_ms,
            end_ms=end_ms,
            market_ids=market_ids,
        )
        if trade_loaded is not None:
            trade_count, trade_available = trade_loaded
            trade_agg, estimated_fill_agg = _run_trade_metrics(
                con,
                trade_available=trade_available,
                benchmark_profile=benchmark_profile,
                profile=profile,
            )

    markout_count = 0
    markout_agg: dict[int, dict[str, Any]] = {}
    if markout_valid:
        loaded_markouts = _prepare_markout_dataset(
            con,
            markout_valid=markout_valid,
            start_ms=start_ms,
            end_ms=end_ms,
            market_ids=market_ids,
        )
        if loaded_markouts is not None:
            markout_count = loaded_markouts
            markout_agg = _run_markout_metrics(
                con,
                markout_count=markout_count,
                benchmark_profile=benchmark_profile,
                profile=profile,
            )

    rows = _merge_market_rows(
        book_agg,
        persistence,
        volatility,
        trade_agg,
        markout_agg,
        estimated_fill_agg,
        hours,
    )

    t_score = time.monotonic()
    thresholds = CandidateThresholds(
        min_coverage_pct=settings.min_coverage_pct,
        min_trades_per_hour=settings.min_trades_per_hour,
        min_two_sided_depth_10bps_usd=settings.min_two_sided_depth_10bps_usd,
        min_median_spread_bps=settings.min_median_spread_bps,
        min_markout_samples_5s=settings.min_markout_samples_5s,
        min_markout_samples_30s=settings.min_markout_samples_30s,
        min_median_trades_per_minute=settings.min_median_trades_per_minute,
        min_observation_hours=settings.min_observation_hours_for_candidate,
    )
    scoring_peers = _scoring_peer_rows(rows, peer_rows)
    scored = score_markets(rows, thresholds=thresholds, peer_rows=scoring_peers)
    rss = _rss_mb()
    if benchmark_profile:
        profile["rss_after_score_mb"] = rss
    log.info(
        "analysis phase=score elapsed=%.3fs total_elapsed=%.3fs rss_mb=%.1f",
        time.monotonic() - t_score,
        time.monotonic() - t0,
        rss,
    )

    if settings.paper_mm_enabled:
        t_paper = time.monotonic()
        try:
            run_paper_mm_for_scored(
                con,
                scored,
                settings,
                start_ms,
                end_ms,
                hours,
                market_ids_override=paper_mm_market_ids,
                top_n_override=paper_mm_top_n_override,
                order_usd_override=paper_mm_order_usd_override,
            )
        except Exception:
            log.exception("paper_mm batch failed")
        log.info(
            "analysis phase=paper_mm elapsed=%.3fs rss_mb=%.1f",
            time.monotonic() - t_paper,
            _rss_mb(),
        )
    log.info(
        "analysis completed valid_files=%s corrupt_files=%s status=%s",
        parquet_health["valid_parquet_files"],
        parquet_health["corrupt_parquet_files"],
        parquet_health["status"],
    )
    result: dict[str, Any] = {
        "hours": hours,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "markets": rows,
        "scored": scored,
        "candidates": [s for s in scored if s.candidate],
        "avoid": avoid_wide_spread_markets(scored, 10),
        "book_row_count": book_count,
        "latest_book_event_ms": latest_book_event_ms,
        "trade_row_count": trade_count,
        "markout_row_count": markout_count,
        "parquet_health": parquet_health,
    }
    if benchmark_profile:
        result["benchmark_profile"] = profile
    if explain_volatility:
        result["volatility_explain"] = _volatility_explain_plans(con, settings)
        try:
            result["estimated_fill_explain"] = estimated_fill_explain_plans(con)
        except Exception as exc:  # noqa: BLE001
            log.warning("estimated fill EXPLAIN failed: %s", exc)
    return result


def analyze_window(
    settings: Settings,
    hours: float,
    *,
    sources: AnalysisSources | None = None,
    benchmark_profile: bool = False,
    explain_volatility: bool = False,
    duckdb_memory_limit: str | None = None,
    duckdb_threads: int | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Backward-compatible lookback wrapper over ``analyze_range``."""
    if hours <= 0:
        return {
            "hours": hours,
            "markets": [],
            "scored": [],
            "error": "hours must be > 0 (use a positive lookback window)",
        }
    con = duckdb.connect(database=":memory:")
    now_ms = con.execute("SELECT CAST(epoch_ms(current_timestamp) AS BIGINT)").fetchone()[0]
    start_ms = now_ms - int(hours * 3600 * 1000)
    return analyze_range(
        settings,
        start_ms=start_ms,
        end_ms=now_ms,
        sources=sources,
        benchmark_profile=benchmark_profile,
        explain_volatility=explain_volatility,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_threads=duckdb_threads,
        read_only=read_only,
    )


def _merge_market_rows(
    book_agg: dict[int, dict[str, Any]],
    persistence: dict[int, dict[str, Any]],
    volatility: dict[int, dict[str, Any]],
    trade_agg: dict[int, dict[str, Any]],
    markout_agg: dict[int, dict[str, Any]],
    estimated_fill_agg: dict[int, dict[str, Any]],
    hours: float,
) -> list[dict[str, Any]]:
    all_ids = set(book_agg) | set(trade_agg) | set(markout_agg) | set(estimated_fill_agg)
    rows: list[dict[str, Any]] = []
    for mid in sorted(all_ids):
        row: dict[str, Any] = {
            "market_id": mid,
            "analysis_window_hours": hours,
            "analysis_scope": "rolling",
            "observation_hours": 0.0,
        }
        if mid in book_agg:
            row.update(book_agg[mid])
        else:
            row["symbol"] = trade_agg.get(mid, {}).get("symbol") or markout_agg.get(mid, {}).get("symbol")
            row["data_coverage_pct"] = 0.0
            row["observation_hours"] = 0.0
        row.setdefault("analysis_window_hours", hours)
        row.setdefault("analysis_scope", "rolling")
        row.setdefault("observation_hours", 0.0)
        row.update(persistence.get(mid, {}))
        row.update(volatility.get(mid, {}))
        row.update(trade_agg.get(mid, _empty_trade_stats()))
        row.update(markout_agg.get(mid, _empty_markout_stats()))
        m5c = int(row.get("markout_5s_count") or 0)
        m30c = int(row.get("markout_30s_count") or 0)
        row["markout_sample_quality"] = sample_quality(min(m5c, m30c))
        row.update(estimated_fill_agg.get(mid, empty_estimated_fill_stats()))
        # Maker fee is not on the analyzer row path today; edge excludes fee (0).
        attach_estimated_maker_edge(row)
        rows.append(row)
    return rows


def scored_to_records(scored: list[ScoredMarket]) -> list[dict[str, Any]]:
    records = []
    for s in scored:
        rec = dict(s.row)
        rec.update(
            {
                "mm_opportunity_score": s.score,
                "letter_rank": s.letter_rank,
                "is_candidate": s.candidate,
                "recommended_max_order_usd": s.recommended_max_order_usd,
                "pros": " | ".join(s.pros),
                "cons": " | ".join(s.cons),
                "warnings": " | ".join(s.warnings),
                **s.size_fit,
                **{f"pct_{k}": v for k, v in s.rank_components.items()},
            }
        )
        records.append(rec)
    return records
