"""DuckDB SQL aggregation over collected Parquet datasets (memory-safe for 72h)."""

from __future__ import annotations

import logging
import resource
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from lighter_mm.config import Settings
from lighter_mm.scoring import (
    CandidateThresholds,
    ScoredMarket,
    avoid_wide_spread_markets,
    score_markets,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisSources:
    """Parquet source directories for DuckDB aggregation."""

    books: Path
    trades: Path
    markouts: Path


def _default_sources(data_dir: Path) -> AnalysisSources:
    return AnalysisSources(
        books=data_dir / "book_samples",
        trades=data_dir / "trades",
        markouts=data_dir / "markouts",
    )


def _connect(
    data_dir: Path,
    *,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute(f"SET threads TO {threads or 2}")
    con.execute(f"SET memory_limit='{memory_limit or '512MB'}'")
    return con


def _glob_patterns(path: Path) -> list[str]:
    """Locate Parquet parts under hive partitions."""
    patterns: list[str] = []
    if list(path.glob("date=*/hour=*/*.parquet")):
        patterns.append(str(path / "date=*/hour=*/*.parquet"))
    if list(path.glob("date=*/*.parquet")):
        patterns.append(str(path / "date=*/*.parquet"))
    return patterns


def _glob_or_none(path: Path) -> str | None:
    patterns = _glob_patterns(path)
    return patterns[0] if patterns else None


def _parquet_list(patterns: list[str]) -> str:
    return "[" + ", ".join("'" + p.replace("'", "''") + "'" for p in patterns) + "]"


def _probe_parquet_columns(
    con: duckdb.DuckDBPyConnection, patterns: list[str]
) -> set[str]:
    if not patterns:
        return set()
    listed = _parquet_list(patterns)
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({listed}, hive_partitioning=1, union_by_name=true)"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _book_projection(available: set[str]) -> str:
    depth25 = (
        "two_sided_depth_25bps_usd"
        if "two_sided_depth_25bps_usd" in available
        else "CAST(NULL AS DOUBLE) AS two_sided_depth_25bps_usd"
    )
    return f"""
        timestamp_ms, market_id, symbol, is_stale, spread_bps, mid,
        best_bid_size_usd, best_ask_size_usd,
        two_sided_depth_5bps_usd, two_sided_depth_10bps_usd,
        {depth25},
        current_funding_rate, funding_rate, open_interest, daily_quote_token_volume
    """


def _read_view(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    patterns: list[str],
    columns: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    """Create a DuckDB view over parquet with time window + column projection."""
    if not patterns:
        return False
    listed = _parquet_list(patterns)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT {columns}
        FROM read_parquet({listed}, hive_partitioning=1, union_by_name=true)
        WHERE timestamp_ms >= {start_ms}
          AND timestamp_ms <= {end_ms}
        """
    )
    return True


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def _volatility_explain_plans(
    con: duckdb.DuckDBPyConnection, settings: Settings
) -> dict[str, str]:
    """Return EXPLAIN ANALYZE text for forward-horizon volatility queries."""
    tolerance_ms = max(int(settings.book_sample_interval_seconds * 1500), 2500)
    plans: dict[str, str] = {}
    for horizon_s, label in [(5, "5s"), (30, "30s"), (60, "60s")]:
        horizon_ms = horizon_s * 1000
        sql = f"""
        WITH origins AS (
            SELECT market_id, timestamp_ms AS origin_ts, mid AS mid0
            FROM book_good
            WHERE mid IS NOT NULL AND mid > 0
        ),
        paired AS (
            SELECT
                o.market_id,
                o.origin_ts,
                o.mid0,
                (
                    SELECT b.mid
                    FROM book_good b
                    WHERE b.market_id = o.market_id
                      AND b.timestamp_ms >= o.origin_ts + {horizon_ms}
                      AND b.timestamp_ms <= o.origin_ts + {horizon_ms} + {tolerance_ms}
                      AND b.mid > 0
                    ORDER BY b.timestamp_ms ASC
                    LIMIT 1
                ) AS mid1
            FROM origins o
        )
        SELECT market_id, COUNT(*) FROM paired WHERE mid1 IS NOT NULL GROUP BY market_id
        """
        rows = con.execute(f"EXPLAIN ANALYZE {sql}").fetchall()
        plans[label] = "\n".join(str(r[1] if len(r) > 1 else r[0]) for r in rows)
    return plans


def analyze_range(
    settings: Settings,
    *,
    start_ms: int,
    end_ms: int,
    sources: AnalysisSources | None = None,
    benchmark_profile: bool = False,
    explain_volatility: bool = False,
    duckdb_memory_limit: str | None = None,
    duckdb_threads: int | None = None,
) -> dict[str, Any]:
    """Aggregate Parquet over an explicit time window and source paths."""
    if end_ms <= start_ms:
        return {
            "hours": 0.0,
            "markets": [],
            "scored": [],
            "error": "invalid analysis window (end_ms <= start_ms)",
        }
    src = sources or _default_sources(settings.data_dir)
    con = _connect(
        settings.data_dir,
        memory_limit=duckdb_memory_limit,
        threads=duckdb_threads,
    )
    hours = max((end_ms - start_ms) / 3_600_000.0, 0.001)
    t0 = time.monotonic()

    book_globs = _glob_patterns(src.books)
    trade_globs = _glob_patterns(src.trades)
    markout_globs = _glob_patterns(src.markouts)

    if not book_globs:
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": "no book_samples yet",
        }

    available_cols = _probe_parquet_columns(con, book_globs)
    book_cols = _book_projection(available_cols)
    if not _read_view(con, "book_raw", book_globs, book_cols, start_ms, end_ms):
        return {
            "hours": hours,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "markets": [],
            "scored": [],
            "error": "no book_samples yet",
        }

    # Dedupe book samples on (market_id, timestamp_ms) — restart/hydrate overlap defense.
    con.execute(
        """
        CREATE OR REPLACE VIEW book_deduped AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY market_id, timestamp_ms ORDER BY timestamp_ms
            ) AS rn
            FROM book_raw
        ) WHERE rn = 1
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW book_good AS
        SELECT * FROM book_deduped
        WHERE is_stale = false
          AND mid IS NOT NULL
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW market_windows AS
        SELECT
            market_id,
            MAX(symbol) AS symbol,
            MIN(timestamp_ms) AS first_observed_ms,
            GREATEST(CAST({start_ms} AS BIGINT), MIN(timestamp_ms)) AS effective_start_ms,
            CAST({end_ms} AS BIGINT) AS effective_end_ms,
            CAST(
                CAST({end_ms} AS BIGINT) - GREATEST(CAST({start_ms} AS BIGINT), MIN(timestamp_ms))
                AS DOUBLE
            ) / 1000.0 AS market_observation_seconds
        FROM book_deduped
        GROUP BY market_id
        """
    )

    book_count = con.execute("SELECT COUNT(*) FROM book_deduped").fetchone()[0]
    profile: dict[str, float] = {}
    if benchmark_profile:
        profile["rss_after_book_load_mb"] = _rss_mb()
    log.info(
        "analysis phase=book_load elapsed=%.3fs book_rows=%s",
        time.monotonic() - t0,
        book_count,
    )

    t_book = time.monotonic()
    book_agg = _aggregate_book_sql(con, settings, start_ms, end_ms, hours)
    if benchmark_profile:
        profile["rss_after_book_aggregate_mb"] = _rss_mb()
    log.info("analysis phase=book_aggregate elapsed=%.3fs markets=%s", time.monotonic() - t_book, len(book_agg))

    t_persist = time.monotonic()
    persistence = _spread_persistence_sql(
        con, settings.spread_thresholds_bps, settings.book_sample_interval_seconds
    )
    if benchmark_profile:
        profile["rss_after_spread_persistence_mb"] = _rss_mb()
    log.info("analysis phase=spread_persistence elapsed=%.3fs", time.monotonic() - t_persist)

    t_vol = time.monotonic()
    volatility = _volatility_sql(con, settings)
    if benchmark_profile:
        profile["rss_after_volatility_mb"] = _rss_mb()
    log.info("analysis phase=volatility elapsed=%.3fs", time.monotonic() - t_vol)

    trade_count = 0
    trade_agg: dict[int, dict[str, Any]] = {}
    if trade_globs:
        trade_cols = "timestamp_ms, market_id, symbol, trade_id, usd_amount, type"
        if _read_view(con, "trade_raw", trade_globs, trade_cols, start_ms, end_ms):
            trade_count = con.execute("SELECT COUNT(*) FROM trade_raw").fetchone()[0]
            t_trade = time.monotonic()
            trade_agg = _aggregate_trades_sql(con)
            if benchmark_profile:
                profile["rss_after_trade_aggregate_mb"] = _rss_mb()
            log.info(
                "analysis phase=trade_aggregate elapsed=%.3fs trade_rows=%s",
                time.monotonic() - t_trade,
                trade_count,
            )

    markout_count = 0
    markout_agg: dict[int, dict[str, Any]] = {}
    if markout_globs:
        markout_cols = (
            "timestamp_ms, market_id, symbol, trade_id, horizon_s, maker_markout_bps"
        )
        if _read_view(con, "markout_raw", markout_globs, markout_cols, start_ms, end_ms):
            markout_count = con.execute("SELECT COUNT(*) FROM markout_raw").fetchone()[0]
            t_markout = time.monotonic()
            markout_agg = _aggregate_markouts_sql(con)
            if benchmark_profile:
                profile["rss_after_markout_aggregate_mb"] = _rss_mb()
            log.info(
                "analysis phase=markout_aggregate elapsed=%.3fs markout_rows=%s",
                time.monotonic() - t_markout,
                markout_count,
            )

    rows = _merge_market_rows(book_agg, persistence, volatility, trade_agg, markout_agg, hours)

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
    scored = score_markets(rows, thresholds=thresholds)
    if benchmark_profile:
        profile["rss_after_score_mb"] = _rss_mb()
    log.info(
        "analysis phase=score elapsed=%.3fs total_elapsed=%.3fs",
        time.monotonic() - t_score,
        time.monotonic() - t0,
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
        "trade_row_count": trade_count,
        "markout_row_count": markout_count,
    }
    if benchmark_profile:
        result["benchmark_profile"] = profile
    if explain_volatility:
        result["volatility_explain"] = _volatility_explain_plans(con, settings)
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
    )


def _aggregate_book_sql(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    start_ms: int,
    end_ms: int,
    hours: float,
) -> dict[int, dict[str, Any]]:
    """Per-market book aggregates via DuckDB GROUP BY."""
    interval_ms = int(settings.book_sample_interval_seconds * 1000)

    all_markets = con.execute(
        "SELECT market_id, symbol, first_observed_ms, market_observation_seconds "
        "FROM market_windows"
    ).fetchall()

    sql = f"""
    WITH agg AS (
        SELECT
            b.market_id,
            MAX(b.symbol) AS symbol,
            COUNT(*) AS usable_samples,
            AVG(b.spread_bps) AS mean_spread_bps,
            quantile_cont(b.spread_bps, 0.5) AS median_spread_bps,
            quantile_cont(b.spread_bps, 0.10) AS p10_spread_bps,
            quantile_cont(b.spread_bps, 0.25) AS p25_spread_bps,
            quantile_cont(b.spread_bps, 0.75) AS p75_spread_bps,
            quantile_cont(b.spread_bps, 0.90) AS p90_spread_bps,
            quantile_cont(b.spread_bps, 0.95) AS p95_spread_bps,
            quantile_cont(LEAST(b.best_bid_size_usd, b.best_ask_size_usd), 0.5)
                AS median_bbo_depth_usd,
            quantile_cont(b.two_sided_depth_5bps_usd, 0.5) AS median_two_sided_depth_5bps_usd,
            quantile_cont(b.two_sided_depth_10bps_usd, 0.5) AS median_two_sided_depth_10bps_usd,
            quantile_cont(b.two_sided_depth_25bps_usd, 0.5) AS median_two_sided_depth_25bps_usd,
            arg_max(b.current_funding_rate, b.timestamp_ms) AS current_funding_rate,
            arg_max(b.funding_rate, b.timestamp_ms) AS funding_rate,
            arg_max(b.open_interest, b.timestamp_ms) AS open_interest,
            arg_max(b.daily_quote_token_volume, b.timestamp_ms) AS daily_quote_volume_usd
        FROM book_good b
        GROUP BY b.market_id
    )
    SELECT
        mw.market_id,
        COALESCE(a.symbol, mw.symbol) AS symbol,
        COALESCE(a.usable_samples, 0) AS usable_samples,
        a.mean_spread_bps,
        a.median_spread_bps,
        a.p10_spread_bps,
        a.p25_spread_bps,
        a.p75_spread_bps,
        a.p90_spread_bps,
        a.p95_spread_bps,
        a.median_bbo_depth_usd,
        a.median_two_sided_depth_5bps_usd,
        a.median_two_sided_depth_10bps_usd,
        a.median_two_sided_depth_25bps_usd,
        a.current_funding_rate,
        a.funding_rate,
        a.open_interest,
        a.daily_quote_volume_usd,
        mw.first_observed_ms,
        mw.market_observation_seconds,
        LEAST(
            100.0,
            100.0 * COALESCE(a.usable_samples, 0) / GREATEST(
                1,
                CAST(mw.market_observation_seconds * 1000 / {interval_ms} AS DOUBLE) + 1
            )
        ) AS data_coverage_pct
    FROM market_windows mw
    LEFT JOIN agg a ON mw.market_id = a.market_id
    """
    result = con.execute(sql).fetchall()
    cols = [
        "market_id", "symbol", "usable_samples", "mean_spread_bps", "median_spread_bps",
        "p10_spread_bps", "p25_spread_bps", "p75_spread_bps", "p90_spread_bps", "p95_spread_bps",
        "median_bbo_depth_usd", "median_two_sided_depth_5bps_usd",
        "median_two_sided_depth_10bps_usd", "median_two_sided_depth_25bps_usd",
        "current_funding_rate", "funding_rate", "open_interest", "daily_quote_volume_usd",
        "first_observed_ms", "market_observation_seconds", "data_coverage_pct",
    ]
    out: dict[int, dict[str, Any]] = {}
    seen: set[int] = set()
    for row in result:
        d = dict(zip(cols, row, strict=True))
        mid = int(d.pop("market_id"))
        obs_s = float(d.pop("market_observation_seconds") or 0)
        d["observation_hours"] = obs_s / 3600.0
        d["analysis_window_hours"] = hours
        out[mid] = d
        seen.add(mid)

    for mid, symbol, first_obs, obs_s in all_markets:
        if int(mid) in seen:
            continue
        obs = float(obs_s or 0)
        out[int(mid)] = {
            "symbol": symbol,
            "observation_hours": obs / 3600.0,
            "analysis_window_hours": hours,
            "first_observed_ms": first_obs,
            "data_coverage_pct": 0.0,
            "usable_samples": 0,
        }
    return out


def _spread_persistence_sql(
    con: duckdb.DuckDBPyConnection,
    thresholds: list[float],
    sample_interval_seconds: float,
) -> dict[int, dict[str, Any]]:
    """Time-weighted spread persistence via DuckDB window functions."""
    if not thresholds:
        return {}
    default_dt = float(sample_interval_seconds)
    threshold_cases = []
    for t in thresholds:
        key = int(t) if float(t).is_integer() else t
        threshold_cases.append(
            f"SUM(CASE WHEN spread_bps >= {t} THEN dt_sec ELSE 0 END) AS above_{key}"
        )
    cases_sql = ",\n            ".join(threshold_cases)
    sql = f"""
    WITH spreads AS (
        SELECT
            market_id,
            timestamp_ms,
            spread_bps,
            LEAD(timestamp_ms) OVER (
                PARTITION BY market_id ORDER BY timestamp_ms
            ) AS next_ts
        FROM book_good
        WHERE spread_bps IS NOT NULL
    ),
    intervals AS (
        SELECT
            market_id,
            spread_bps,
            CASE
                WHEN next_ts IS NULL THEN {default_dt}
                WHEN (next_ts - timestamp_ms) / 1000.0 <= 0
                    OR (next_ts - timestamp_ms) / 1000.0 > 60 THEN {default_dt}
                ELSE (next_ts - timestamp_ms) / 1000.0
            END AS dt_sec
        FROM spreads
    ),
    totals AS (
        SELECT
            market_id,
            SUM(dt_sec) AS total_sec,
            {cases_sql}
        FROM intervals
        GROUP BY market_id
    )
    SELECT * FROM totals
    """
    rows = con.execute(sql).fetchall()
    if not rows:
        return {}
    col_names = ["market_id", "total_sec"] + [
        f"above_{int(t) if float(t).is_integer() else t}" for t in thresholds
    ]
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        d = dict(zip(col_names, row, strict=True))
        mid = int(d.pop("market_id"))
        total = float(d.pop("total_sec") or 0)
        result: dict[str, Any] = {}
        for t in thresholds:
            key = int(t) if float(t).is_integer() else t
            above = float(d.get(f"above_{key}") or 0)
            result[f"pct_time_spread_ge_{key}bps"] = (above / total) if total > 0 else None
            # Event counts via separate lightweight query would be expensive;
            # approximate from threshold crossings in Python is avoided — set None for events.
            result[f"spread_ge_{key}bps_event_count"] = None
            result[f"spread_ge_{key}bps_median_duration_seconds"] = None
            result[f"spread_ge_{key}bps_p90_duration_seconds"] = None
            result[f"spread_ge_{key}bps_max_duration_seconds"] = None
        out[mid] = result
    return out


def _volatility_sql(con: duckdb.DuckDBPyConnection, settings: Settings) -> dict[int, dict[str, Any]]:
    """Per-market volatility from mid series — SQL-only, sampling-interval tolerance."""
    tolerance_ms = max(int(settings.book_sample_interval_seconds * 1500), 2500)
    horizons = [(1, "1s"), (5, "5s"), (30, "30s"), (60, "60s")]
    out: dict[int, dict[str, Any]] = {}

    # 1s proxy: consecutive sample moves (same as before).
    sql_consec = """
    WITH mids AS (
        SELECT market_id, timestamp_ms, mid,
            LAG(mid) OVER (PARTITION BY market_id ORDER BY timestamp_ms) AS prev_mid
        FROM book_good
        WHERE mid IS NOT NULL AND mid > 0
    ),
    moves AS (
        SELECT market_id,
            ABS(LN(mid / prev_mid)) * 10000.0 AS move_bps
        FROM mids
        WHERE prev_mid IS NOT NULL AND prev_mid > 0
    )
    SELECT market_id,
        quantile_cont(move_bps, 0.5) AS p50,
        quantile_cont(move_bps, 0.90) AS p90,
        quantile_cont(move_bps, 0.95) AS p95
    FROM moves
    GROUP BY market_id
    """
    for row in con.execute(sql_consec).fetchall():
        mid, p50, p90, p95 = row
        out.setdefault(int(mid), {})
        out[int(mid)].update({
            "p50_abs_mid_move_1s_bps": p50,
            "p90_abs_mid_move_1s_bps": p90,
            "p95_abs_mid_move_1s_bps": p95,
            "median_abs_mid_move_1s_bps": p50,
        })

    for horizon_s, label in horizons[1:]:
        horizon_ms = horizon_s * 1000
        sql = f"""
        WITH origins AS (
            SELECT market_id, timestamp_ms AS origin_ts, mid AS mid0
            FROM book_good
            WHERE mid IS NOT NULL AND mid > 0
        ),
        paired AS (
            SELECT
                o.market_id,
                o.origin_ts,
                o.mid0,
                (
                    SELECT b.mid
                    FROM book_good b
                    WHERE b.market_id = o.market_id
                      AND b.timestamp_ms >= o.origin_ts + {horizon_ms}
                      AND b.timestamp_ms <= o.origin_ts + {horizon_ms} + {tolerance_ms}
                      AND b.mid > 0
                    ORDER BY b.timestamp_ms ASC
                    LIMIT 1
                ) AS mid1
            FROM origins o
        ),
        moves AS (
            SELECT market_id,
                ABS(LN(mid1 / mid0)) * 10000.0 AS move_bps
            FROM paired
            WHERE mid1 IS NOT NULL AND mid0 > 0
        )
        SELECT market_id,
            COUNT(*) AS sample_count,
            quantile_cont(move_bps, 0.5) AS p50,
            quantile_cont(move_bps, 0.90) AS p90,
            quantile_cont(move_bps, 0.95) AS p95
        FROM moves
        GROUP BY market_id
        """
        for row in con.execute(sql).fetchall():
            mid, sample_count, p50, p90, p95 = row
            out.setdefault(int(mid), {})
            out[int(mid)].update({
                f"volatility_{label}_sample_count": int(sample_count or 0),
                f"p50_abs_mid_move_{label}_bps": p50,
                f"p90_abs_mid_move_{label}_bps": p90,
                f"p95_abs_mid_move_{label}_bps": p95,
                f"median_abs_mid_move_{label}_bps": p50,
            })
    return out


def _aggregate_trades_sql(
    con: duckdb.DuckDBPyConnection,
) -> dict[int, dict[str, Any]]:
    """Per-market trade stats with dedupe on (market_id, trade_id)."""
    from lighter_mm.util import percentile

    con.execute(
        """
        CREATE OR REPLACE VIEW trade_deduped AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY market_id, trade_id ORDER BY timestamp_ms
            ) AS rn
            FROM trade_raw
            WHERE type = 'trade'
        ) WHERE rn = 1
        """
    )
    sql = """
    WITH window_bounds AS (
        SELECT
            market_id,
            FLOOR(effective_start_ms / 60000.0)::BIGINT AS start_minute_idx,
            FLOOR(effective_end_ms / 60000.0)::BIGINT AS end_minute_idx,
            CAST(
                FLOOR(effective_end_ms / 60000.0)
                - FLOOR(effective_start_ms / 60000.0)
                + 1
            AS INTEGER) AS minute_slots,
            market_observation_seconds
        FROM market_windows
    ),
    per_minute AS (
        SELECT
            market_id,
            (timestamp_ms // 60000) AS minute_idx,
            COUNT(*)::DOUBLE AS cnt
        FROM trade_deduped
        GROUP BY market_id, minute_idx
    ),
    slot_rows AS (
        SELECT
            wb.market_id,
            wb.minute_slots,
            wb.market_observation_seconds,
            wb.start_minute_idx + slot_offset AS minute_idx,
            COALESCE(pm.cnt, 0.0) AS cnt
        FROM window_bounds wb
        CROSS JOIN UNNEST(range(wb.minute_slots)) AS u(slot_offset)
        LEFT JOIN per_minute pm
          ON pm.market_id = wb.market_id
         AND pm.minute_idx = wb.start_minute_idx + slot_offset
    ),
    slot_lists AS (
        SELECT
            market_id,
            MAX(minute_slots) AS minute_slots,
            MAX(market_observation_seconds) AS market_observation_seconds,
            LIST(cnt ORDER BY minute_idx) AS cnts,
            COUNT(*) FILTER (WHERE cnt > 0) AS active_minutes
        FROM slot_rows
        GROUP BY market_id
    ),
    trade_agg AS (
        SELECT
            market_id,
            MAX(symbol) AS symbol,
            COUNT(*) AS total_trade_count,
            SUM(usd_amount) AS total_quote_volume,
            quantile_cont(usd_amount, 0.5) AS median_trade_size_usd
        FROM trade_deduped
        GROUP BY market_id
    ),
    intertrade AS (
        SELECT market_id,
            quantile_cont(gap_ms, 0.5) AS median_intertrade_ms
        FROM (
            SELECT market_id,
                timestamp_ms - LAG(timestamp_ms) OVER (
                    PARTITION BY market_id ORDER BY timestamp_ms
                ) AS gap_ms
            FROM trade_deduped
        )
        WHERE gap_ms IS NOT NULL AND gap_ms >= 0
        GROUP BY market_id
    )
    SELECT
        t.market_id,
        t.total_trade_count,
        t.total_quote_volume,
        t.median_trade_size_usd,
        i.median_intertrade_ms,
        sl.cnts,
        sl.active_minutes,
        sl.minute_slots,
        sl.market_observation_seconds
    FROM trade_agg t
    LEFT JOIN intertrade i ON t.market_id = i.market_id
    LEFT JOIN slot_lists sl ON t.market_id = sl.market_id
    """
    out: dict[int, dict[str, Any]] = {}
    for row in con.execute(sql).fetchall():
        mid, tc, vol, med_size, inter_ms, cnts, active_minutes, minute_slots, obs_s = row
        tc = int(tc or 0)
        obs_s = float(obs_s or 0)
        effective_minutes_float = max(obs_s / 60.0, 1.0 / 60.0)
        tpm_mean = float(tc) / effective_minutes_float
        slots = max(1, int(minute_slots or 0))
        cnt_list = [float(c) for c in (cnts or [])]
        if len(cnt_list) < slots:
            cnt_list = cnt_list + [0.0] * (slots - len(cnt_list))
        elif len(cnt_list) > slots:
            cnt_list = cnt_list[:slots]
        out[int(mid)] = {
            "total_trade_count": tc,
            "total_quote_volume": float(vol or 0),
            "median_trade_size_usd": med_size,
            "median_intertrade_ms": inter_ms,
            "trades_per_minute_mean": tpm_mean,
            "trades_per_minute_median": percentile(cnt_list, 50) or 0.0,
            "trades_per_minute_p90": percentile(cnt_list, 90) or 0.0,
        }
    return out


def _aggregate_markouts_sql(con: duckdb.DuckDBPyConnection) -> dict[int, dict[str, Any]]:
    """Per-market markout stats with dedupe on (market_id, trade_id, horizon_s)."""
    sql = """
    WITH deduped AS (
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY market_id, trade_id, horizon_s ORDER BY timestamp_ms
            ) AS rn
            FROM markout_raw
        ) WHERE rn = 1
    )
    SELECT
        market_id,
        horizon_s,
        COUNT(*) AS cnt,
        AVG(maker_markout_bps) AS mean_bps,
        quantile_cont(maker_markout_bps, 0.5) AS median_bps,
        AVG(CASE WHEN maker_markout_bps > 0 THEN 1.0 ELSE 0.0 END) AS pos_ratio
    FROM deduped
    GROUP BY market_id, horizon_s
    """
    out: dict[int, dict[str, Any]] = {}
    for row in con.execute(sql).fetchall():
        mid, horizon_s, cnt, mean_bps, median_bps, pos_ratio = row
        mid = int(mid)
        h = int(horizon_s)
        out.setdefault(mid, {})
        out[mid][f"maker_markout_{h}s_mean_bps"] = mean_bps
        out[mid][f"maker_markout_{h}s_median_bps"] = median_bps
        out[mid][f"markout_{h}s_count"] = int(cnt)
        if h in (5, 30):
            out[mid][f"pct_positive_markout_{h}s"] = pos_ratio
    return out


def _merge_market_rows(
    book_agg: dict[int, dict[str, Any]],
    persistence: dict[int, dict[str, Any]],
    volatility: dict[int, dict[str, Any]],
    trade_agg: dict[int, dict[str, Any]],
    markout_agg: dict[int, dict[str, Any]],
    hours: float,
) -> list[dict[str, Any]]:
    all_ids = set(book_agg) | set(trade_agg) | set(markout_agg)
    rows: list[dict[str, Any]] = []
    for mid in sorted(all_ids):
        row: dict[str, Any] = {
            "market_id": mid,
            "analysis_window_hours": hours,
            "observation_hours": 0.0,
        }
        if mid in book_agg:
            row.update(book_agg[mid])
        else:
            row["symbol"] = trade_agg.get(mid, {}).get("symbol") or markout_agg.get(mid, {}).get("symbol")
            row["data_coverage_pct"] = 0.0
            row["observation_hours"] = 0.0
        row.setdefault("analysis_window_hours", hours)
        row.setdefault("observation_hours", 0.0)
        row.update(persistence.get(mid, {}))
        row.update(volatility.get(mid, {}))
        row.update(trade_agg.get(mid, _empty_trade_stats()))
        row.update(markout_agg.get(mid, _empty_markout_stats()))
        rows.append(row)
    return rows


def _empty_trade_stats() -> dict[str, Any]:
    return {
        "total_trade_count": 0,
        "trades_per_minute_mean": 0.0,
        "trades_per_minute_median": 0.0,
        "trades_per_minute_p90": 0.0,
        "total_quote_volume": 0.0,
        "median_trade_size_usd": None,
        "median_intertrade_ms": None,
    }


def _empty_markout_stats() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in (1, 5, 30, 60):
        out[f"maker_markout_{h}s_mean_bps"] = None
        out[f"maker_markout_{h}s_median_bps"] = None
        out[f"markout_{h}s_count"] = 0
    out["pct_positive_markout_5s"] = None
    out["pct_positive_markout_30s"] = None
    return out


# Backward-compatible helpers for tests that import these directly.
def _spread_persistence(book, thresholds):  # noqa: ANN001
    """Legacy Polars-based spread persistence (unit tests)."""
    import polars as pl

    from lighter_mm.util import percentile

    if not isinstance(book, pl.DataFrame):
        return {f"pct_time_spread_ge_{int(t)}bps": None for t in thresholds}
    spreads = book.select(["timestamp_ms", "spread_bps"]).drop_nulls().sort("timestamp_ms")
    if spreads.is_empty():
        return {f"pct_time_spread_ge_{int(t)}bps": None for t in thresholds}

    ts = spreads["timestamp_ms"].to_list()
    sp = spreads["spread_bps"].to_list()
    total = 0.0
    above = {t: 0.0 for t in thresholds}
    durations: dict[float, list[float]] = {t: [] for t in thresholds}
    open_run: dict[float, float | None] = {t: None for t in thresholds}

    for i in range(len(ts)):
        dt = (ts[i + 1] - ts[i]) / 1000.0 if i + 1 < len(ts) else 5.0
        if dt <= 0 or dt > 60:
            dt = 5.0
        total += dt
        s = sp[i]
        for t in thresholds:
            if s is not None and s >= t:
                above[t] += dt
                if open_run[t] is None:
                    open_run[t] = 0.0
                open_run[t] = (open_run[t] or 0.0) + dt
            else:
                if open_run[t] is not None:
                    durations[t].append(open_run[t] or 0.0)
                    open_run[t] = None
    for t in thresholds:
        if open_run[t] is not None:
            durations[t].append(open_run[t] or 0.0)

    out: dict[str, Any] = {}
    for t in thresholds:
        key = int(t) if float(t).is_integer() else t
        out[f"pct_time_spread_ge_{key}bps"] = (above[t] / total) if total > 0 else None
        durs = durations[t]
        out[f"spread_ge_{key}bps_event_count"] = len(durs)
        out[f"spread_ge_{key}bps_median_duration_seconds"] = percentile(durs, 50)
        out[f"spread_ge_{key}bps_p90_duration_seconds"] = percentile(durs, 90)
        out[f"spread_ge_{key}bps_max_duration_seconds"] = max(durs) if durs else None
    return out


def _trade_stats(
    trade_df,
    market_id: int,
    *,
    window_minutes: int = 1,
    observation_seconds: float | None = None,
    effective_start_ms: int | None = None,
    effective_end_ms: int | None = None,
):  # noqa: ANN001
    """Legacy wrapper for unit tests."""
    import polars as pl

    if not isinstance(trade_df, pl.DataFrame):
        return _empty_trade_stats()
    if "trade_id" not in trade_df.columns:
        trade_df = trade_df.with_columns(pl.arange(0, trade_df.height).alias("trade_id"))
    if "symbol" not in trade_df.columns:
        trade_df = trade_df.with_columns(pl.lit("TEST").alias("symbol"))
    obs_s = float(observation_seconds) if observation_seconds is not None else float(window_minutes * 60)
    if effective_start_ms is None:
        effective_start_ms = int(trade_df["timestamp_ms"].min())
    if effective_end_ms is None:
        effective_end_ms = effective_start_ms + int(obs_s * 1000)
    con = duckdb.connect(":memory:")
    con.register("trade_raw", trade_df)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW market_windows AS
        SELECT
            market_id,
            MAX(symbol) AS symbol,
            MIN(timestamp_ms) AS first_observed_ms,
            {int(effective_start_ms)}::BIGINT AS effective_start_ms,
            {int(effective_end_ms)}::BIGINT AS effective_end_ms,
            {obs_s}::DOUBLE AS market_observation_seconds
        FROM trade_raw
        GROUP BY market_id
        """
    )
    agg = _aggregate_trades_sql(con)
    return agg.get(market_id, _empty_trade_stats())


def _markout_stats(markout_df, market_id: int):  # noqa: ANN001
    """Legacy wrapper for unit tests."""
    import polars as pl

    if not isinstance(markout_df, pl.DataFrame) or markout_df.is_empty():
        return _empty_markout_stats()
    con = duckdb.connect(":memory:")
    con.register("markout_raw", markout_df)
    agg = _aggregate_markouts_sql(con)
    return agg.get(market_id, _empty_markout_stats())


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
