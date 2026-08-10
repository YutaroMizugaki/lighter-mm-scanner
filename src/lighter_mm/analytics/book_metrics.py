"""Book sample aggregation SQL and legacy spread persistence."""

from __future__ import annotations

import logging
from typing import Any

import duckdb

from lighter_mm.config import Settings

log = logging.getLogger(__name__)

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
            FROM book_observed
            WHERE mid IS NOT NULL AND mid > 0
        ),
        candidates AS (
            SELECT
                o.market_id,
                o.origin_ts,
                o.mid0,
                b.timestamp_ms AS ts1,
                b.mid AS mid1
            FROM origins o
            INNER JOIN book_observed b
              ON b.market_id = o.market_id
             AND b.timestamp_ms >= o.origin_ts + {horizon_ms}
             AND b.timestamp_ms <= o.origin_ts + {horizon_ms} + {tolerance_ms}
             AND b.mid IS NOT NULL
             AND b.mid > 0
        ),
        paired AS (
            SELECT market_id, origin_ts, mid0, mid1
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY market_id, origin_ts ORDER BY ts1 ASC
                    ) AS rn
                FROM candidates
            ) ranked
            WHERE rn = 1
        )
        SELECT market_id, COUNT(*) FROM paired WHERE mid1 IS NOT NULL GROUP BY market_id
        """
        rows = con.execute(f"EXPLAIN ANALYZE {sql}").fetchall()
        plans[label] = "\n".join(str(r[1] if len(r) > 1 else r[0]) for r in rows)
    return plans

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
        "SELECT market_id, symbol, first_observed_ms, last_observed_ms, market_observation_seconds "
        "FROM market_windows"
    ).fetchall()

    sql = f"""
    WITH coverage_agg AS (
        SELECT
            market_id,
            MAX(symbol) AS symbol,
            COUNT(*) AS observed_samples,
            COUNT(*) FILTER (
                WHERE is_usable = true OR (is_usable IS NULL AND mid IS NOT NULL)
            ) AS usable_samples
        FROM book_deduped
        GROUP BY market_id
    ),
    spread_cov_agg AS (
        SELECT
            market_id,
            COUNT(*) AS spread_samples
        FROM book_spread_observed
        WHERE effective_spread_bps IS NOT NULL
        GROUP BY market_id
    ),
    metrics_agg AS (
        SELECT
            b.market_id,
            quantile_cont(LEAST(b.best_bid_size_usd, b.best_ask_size_usd), 0.5)
                AS median_bbo_depth_usd,
            quantile_cont(b.two_sided_depth_5bps_usd, 0.5) AS median_two_sided_depth_5bps_usd,
            quantile_cont(b.two_sided_depth_10bps_usd, 0.5) AS median_two_sided_depth_10bps_usd,
            quantile_cont(b.two_sided_depth_25bps_usd, 0.5) AS median_two_sided_depth_25bps_usd,
            arg_max(b.current_funding_rate, b.timestamp_ms) AS current_funding_rate,
            arg_max(b.funding_rate, b.timestamp_ms) AS funding_rate,
            arg_max(b.open_interest, b.timestamp_ms) AS open_interest,
            arg_max(b.daily_quote_token_volume, b.timestamp_ms) AS daily_quote_volume_usd
        FROM book_metrics_good b
        GROUP BY b.market_id
    ),
    spread_agg AS (
        SELECT
            market_id,
            AVG(effective_spread_bps) AS mean_spread_bps,
            quantile_cont(effective_spread_bps, 0.5) AS median_spread_bps,
            quantile_cont(effective_spread_bps, 0.10) AS p10_spread_bps,
            quantile_cont(effective_spread_bps, 0.25) AS p25_spread_bps,
            quantile_cont(effective_spread_bps, 0.75) AS p75_spread_bps,
            quantile_cont(effective_spread_bps, 0.90) AS p90_spread_bps,
            quantile_cont(effective_spread_bps, 0.95) AS p95_spread_bps
        FROM book_spread_observed
        WHERE effective_spread_bps IS NOT NULL
        GROUP BY market_id
    ),
    activity_agg AS (
        SELECT
            market_id,
            arg_max(book_update_age_ms, timestamp_ms) / 1000.0 AS latest_book_update_age_seconds,
            quantile_cont(book_update_age_ms / 1000.0, 0.95) AS p95_book_update_age_seconds
        FROM book_deduped
        WHERE book_update_age_ms IS NOT NULL
        GROUP BY market_id
    )
    SELECT
        mw.market_id,
        COALESCE(c.symbol, mw.symbol) AS symbol,
        COALESCE(c.observed_samples, 0) AS observed_samples,
        COALESCE(c.usable_samples, 0) AS usable_samples,
        COALESCE(sc.spread_samples, 0) AS spread_samples,
        s.mean_spread_bps,
        s.median_spread_bps,
        s.p10_spread_bps,
        s.p25_spread_bps,
        s.p75_spread_bps,
        s.p90_spread_bps,
        s.p95_spread_bps,
        m.median_bbo_depth_usd,
        m.median_two_sided_depth_5bps_usd,
        m.median_two_sided_depth_10bps_usd,
        m.median_two_sided_depth_25bps_usd,
        m.current_funding_rate,
        m.funding_rate,
        m.open_interest,
        m.daily_quote_volume_usd,
        act.latest_book_update_age_seconds,
        act.p95_book_update_age_seconds,
        mw.first_observed_ms,
        mw.last_observed_ms,
        mw.market_observation_seconds,
        CAST(
            GREATEST(
                1,
                CAST(mw.market_observation_seconds * 1000 / {interval_ms} AS DOUBLE) + 1
            ) AS BIGINT
        ) AS expected_samples,
        LEAST(
            100.0,
            100.0 * COALESCE(c.observed_samples, 0) / GREATEST(
                1,
                CAST(mw.market_observation_seconds * 1000 / {interval_ms} AS DOUBLE) + 1
            )
        ) AS observation_coverage_pct,
        CASE
            WHEN COALESCE(c.observed_samples, 0) > 0 THEN LEAST(
                100.0,
                100.0 * COALESCE(c.usable_samples, 0) / COALESCE(c.observed_samples, 0)
            )
            ELSE 0.0
        END AS usable_quote_coverage_pct,
        CASE
            WHEN COALESCE(c.observed_samples, 0) > 0 THEN LEAST(
                100.0,
                100.0 * COALESCE(sc.spread_samples, 0) / COALESCE(c.observed_samples, 0)
            )
            ELSE 0.0
        END AS spread_coverage_pct,
        LEAST(
            100.0,
            100.0 * COALESCE(c.observed_samples, 0) / GREATEST(
                1,
                CAST(mw.market_observation_seconds * 1000 / {interval_ms} AS DOUBLE) + 1
            )
        ) AS data_coverage_pct
    FROM market_windows mw
    LEFT JOIN coverage_agg c ON mw.market_id = c.market_id
    LEFT JOIN spread_agg s ON mw.market_id = s.market_id
    LEFT JOIN spread_cov_agg sc ON mw.market_id = sc.market_id
    LEFT JOIN metrics_agg m ON mw.market_id = m.market_id
    LEFT JOIN activity_agg act ON mw.market_id = act.market_id
    """
    result = con.execute(sql).fetchall()
    cols = [
        "market_id", "symbol", "observed_samples", "usable_samples", "spread_samples",
        "mean_spread_bps", "median_spread_bps",
        "p10_spread_bps", "p25_spread_bps", "p75_spread_bps", "p90_spread_bps", "p95_spread_bps",
        "median_bbo_depth_usd", "median_two_sided_depth_5bps_usd",
        "median_two_sided_depth_10bps_usd", "median_two_sided_depth_25bps_usd",
        "current_funding_rate", "funding_rate", "open_interest", "daily_quote_volume_usd",
        "latest_book_update_age_seconds", "p95_book_update_age_seconds",
        "first_observed_ms", "last_observed_ms", "market_observation_seconds", "expected_samples",
        "observation_coverage_pct", "usable_quote_coverage_pct", "spread_coverage_pct",
        "data_coverage_pct",
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

    for mid, symbol, first_obs, last_obs, obs_s in all_markets:
        if int(mid) in seen:
            continue
        obs = float(obs_s or 0)
        out[int(mid)] = {
            "symbol": symbol,
            "observation_hours": obs / 3600.0,
            "analysis_window_hours": hours,
            "first_observed_ms": first_obs,
            "last_observed_ms": last_obs,
            "data_coverage_pct": 0.0,
            "observation_coverage_pct": 0.0,
            "usable_quote_coverage_pct": 0.0,
            "spread_coverage_pct": 0.0,
            "observed_samples": 0,
            "usable_samples": 0,
            "spread_samples": 0,
            "expected_samples": 0,
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
            effective_spread_bps AS spread_bps,
            LEAD(timestamp_ms) OVER (
                PARTITION BY market_id ORDER BY timestamp_ms
            ) AS next_ts
        FROM book_spread_observed
        WHERE effective_spread_bps IS NOT NULL
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
        FROM book_observed
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
        # Hash/range JOIN + ROW_NUMBER (not correlated subqueries). The previous
        # per-row scalar subquery over book_observed OOMed Analyzer at multi-million
        # row scale even with a 1GiB DuckDB memory_limit.
        sql = f"""
        WITH origins AS (
            SELECT market_id, timestamp_ms AS origin_ts, mid AS mid0
            FROM book_observed
            WHERE mid IS NOT NULL AND mid > 0
        ),
        candidates AS (
            SELECT
                o.market_id,
                o.origin_ts,
                o.mid0,
                b.timestamp_ms AS ts1,
                b.mid AS mid1
            FROM origins o
            INNER JOIN book_observed b
              ON b.market_id = o.market_id
             AND b.timestamp_ms >= o.origin_ts + {horizon_ms}
             AND b.timestamp_ms <= o.origin_ts + {horizon_ms} + {tolerance_ms}
             AND b.mid IS NOT NULL
             AND b.mid > 0
        ),
        paired AS (
            SELECT market_id, origin_ts, mid0, mid1
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY market_id, origin_ts ORDER BY ts1 ASC
                    ) AS rn
                FROM candidates
            ) ranked
            WHERE rn = 1
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
