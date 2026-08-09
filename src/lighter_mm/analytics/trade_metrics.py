"""Trade aggregation SQL and legacy test wrappers."""

from __future__ import annotations

from typing import Any

import duckdb


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
