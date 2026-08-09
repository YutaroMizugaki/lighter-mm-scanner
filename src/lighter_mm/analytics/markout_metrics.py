"""Markout aggregation SQL and legacy test wrappers."""

from __future__ import annotations

from typing import Any

import duckdb


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

def _empty_markout_stats() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in (1, 5, 30, 60):
        out[f"maker_markout_{h}s_mean_bps"] = None
        out[f"maker_markout_{h}s_median_bps"] = None
        out[f"markout_{h}s_count"] = 0
    out["pct_positive_markout_5s"] = None
    out["pct_positive_markout_30s"] = None
    return out

def _markout_stats(markout_df, market_id: int):  # noqa: ANN001
    """Legacy wrapper for unit tests."""
    import polars as pl

    if not isinstance(markout_df, pl.DataFrame) or markout_df.is_empty():
        return _empty_markout_stats()
    con = duckdb.connect(":memory:")
    con.register("markout_raw", markout_df)
    agg = _aggregate_markouts_sql(con)
    return agg.get(market_id, _empty_markout_stats())
