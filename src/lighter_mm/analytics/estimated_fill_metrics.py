"""Estimated Maker Fill — analyzer-only virtual touch-quote opportunity metrics.

Uses already-materialized usable book snapshots and regular trades.
This is NOT actual fill probability: queue position, cancels/amends, and
own-order lifecycle are not observed.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

# Ranking default hypothetical order size (USD).
DEFAULT_ORDER_USD = 50.0
ORDER_SIZES_USD = (25.0, 50.0, 100.0)
HORIZONS_S = (5, 30)
# Estimated-fill-only downsample; does not change collector book sampling.
SNAPSHOT_BUCKET_MS = 30_000
MIN_MEANINGFUL_SAMPLES = 100


def sample_quality(n: int | None) -> str:
    """Classify observation count for Estimated Fill / markout reliability labels."""
    count = int(n or 0)
    if count < 100:
        return "insufficient"
    if count < 500:
        return "preliminary"
    return "reliable"


def maker_fee_rate_to_bps(maker_fee: float | None) -> float:
    """Convert Lighter API maker_fee decimal rate to basis points.

    API examples use values like ``0.00010`` (1 bp) / ``0.00045`` (4.5 bp):
    fraction of notional, not percent points. bps = rate * 10_000.
    """
    if maker_fee is None:
        return 0.0
    return float(maker_fee) * 10_000.0


def estimated_maker_edge_bps(
    *,
    fill_rate: float | None,
    median_spread_bps: float | None,
    maker_markout_bps: float | None,
    maker_fee_bps: float = 0.0,
) -> float | None:
    """fill_rate × (half_spread + markout − fee). Not expected profit."""
    if fill_rate is None or median_spread_bps is None or maker_markout_bps is None:
        return None
    edge_per_fill = (float(median_spread_bps) / 2.0) + float(maker_markout_bps) - float(
        maker_fee_bps
    )
    return float(fill_rate) * edge_per_fill


def max_estimated_fill_snapshots(window_hours: float, bucket_ms: int = SNAPSHOT_BUCKET_MS) -> int:
    """Upper bound on downsampled snapshots for one market over an analysis window."""
    if window_hours <= 0:
        return 0
    window_ms = window_hours * 3_600_000.0
    return int(math.ceil(window_ms / float(bucket_ms)))


def _empty_estimated_fill_stats() -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for size in ORDER_SIZES_USD:
        size_key = str(int(size))
        detail[size_key] = {}
        for horizon in HORIZONS_S:
            detail[size_key][f"{horizon}s"] = {
                "optimistic": None,
                "conservative": None,
                "bid_optimistic": None,
                "bid_conservative": None,
                "ask_optimistic": None,
                "ask_conservative": None,
            }
    return {
        "estimated_maker_fill_samples": 0,
        "estimated_maker_fill_sample_quality": "insufficient",
        "estimated_maker_fill_rate_5s_conservative": None,
        "estimated_maker_fill_rate_30s_conservative": None,
        "estimated_maker_fill_rate_5s_optimistic": None,
        "estimated_maker_fill_rate_30s_optimistic": None,
        "estimated_maker_fill_by_size": detail,
        "estimated_maker_edge_5s_bps": None,
        "estimated_maker_edge_30s_bps": None,
        "estimated_maker_edge_fee_included": False,
    }


def _downsample_snapshots_sql(bucket_ms: int = SNAPSHOT_BUCKET_MS) -> str:
    return f"""
        CREATE OR REPLACE TABLE estimated_fill_snapshots AS
        SELECT
            market_id,
            symbol,
            timestamp_ms,
            best_bid,
            best_ask,
            best_bid_size_usd,
            best_ask_size_usd
        FROM (
            SELECT
                market_id,
                symbol,
                timestamp_ms,
                best_bid,
                best_ask,
                best_bid_size_usd,
                best_ask_size_usd,
                ROW_NUMBER() OVER (
                    PARTITION BY market_id, CAST(timestamp_ms / {bucket_ms} AS BIGINT)
                    ORDER BY timestamp_ms ASC
                ) AS rn
            FROM book_observed
            WHERE (is_usable = true OR (is_usable IS NULL AND mid IS NOT NULL))
              AND best_bid IS NOT NULL
              AND best_ask IS NOT NULL
              AND best_bid_size_usd IS NOT NULL
              AND best_ask_size_usd IS NOT NULL
        ) ranked
        WHERE rn = 1
        """


def _side_eligible_sql(side: str, horizon_ms: int = 30_000) -> str:
    """Aggregate eligible aggressive notional per snapshot for one maker side.

    Buy maker (bid): taker sells → is_maker_ask = false, price <= best_bid.
    Sell maker (ask): taker buys → is_maker_ask = true, price >= best_ask.
    """
    if side == "bid":
        maker_ask_pred = "t.is_maker_ask = false"
        price_pred = "t.price <= s.best_bid"
        size_col = "s.best_bid_size_usd"
        px_col = "s.best_bid"
    elif side == "ask":
        maker_ask_pred = "t.is_maker_ask = true"
        price_pred = "t.price >= s.best_ask"
        size_col = "s.best_ask_size_usd"
        px_col = "s.best_ask"
    else:
        raise ValueError(f"unknown side: {side}")

    return f"""
        CREATE OR REPLACE TABLE estimated_fill_{side}_eligible AS
        SELECT
            s.market_id,
            s.timestamp_ms,
            {px_col} AS touch_px,
            {size_col} AS touch_size_usd,
            COALESCE(SUM(t.usd_amount) FILTER (
                WHERE t.timestamp_ms <= s.timestamp_ms + 5000
            ), 0.0) AS eligible_usd_5s,
            COALESCE(SUM(t.usd_amount) FILTER (
                WHERE t.timestamp_ms <= s.timestamp_ms + {horizon_ms}
            ), 0.0) AS eligible_usd_30s
        FROM estimated_fill_snapshots s
        LEFT JOIN trade_deduped t
          ON t.market_id = s.market_id
         AND {maker_ask_pred}
         AND t.timestamp_ms > s.timestamp_ms
         AND t.timestamp_ms <= s.timestamp_ms + {horizon_ms}
         AND t.price IS NOT NULL
         AND t.usd_amount IS NOT NULL
         AND {price_pred}
        GROUP BY s.market_id, s.timestamp_ms, {px_col}, {size_col}
        """


def _fill_flag_expr(eligible_col: str, touch_size_col: str, order_usd: float, mode: str) -> str:
    if mode == "optimistic":
        required = f"{order_usd}"
    elif mode == "conservative":
        required = f"({touch_size_col} + {order_usd})"
    else:
        raise ValueError(mode)
    return f"CASE WHEN {eligible_col} >= {required} THEN 1.0 ELSE 0.0 END"


def _aggregate_fill_rates_sql() -> str:
    """Market-level fill rates: min(bid_rate, ask_rate) per size/horizon/mode."""
    selects: list[str] = [
        "b.market_id",
        "COUNT(*)::BIGINT AS estimated_maker_fill_samples",
    ]
    for size in ORDER_SIZES_USD:
        size_i = int(size)
        for horizon, elig in ((5, "eligible_usd_5s"), (30, "eligible_usd_30s")):
            for mode in ("optimistic", "conservative"):
                bid_flag = _fill_flag_expr(f"b.{elig}", "b.touch_size_usd", size, mode)
                ask_flag = _fill_flag_expr(f"a.{elig}", "a.touch_size_usd", size, mode)
                selects.append(
                    f"AVG({bid_flag}) AS bid_{mode}_{horizon}s_{size_i}"
                )
                selects.append(
                    f"AVG({ask_flag}) AS ask_{mode}_{horizon}s_{size_i}"
                )
                selects.append(
                    f"LEAST(AVG({bid_flag}), AVG({ask_flag})) AS mkt_{mode}_{horizon}s_{size_i}"
                )

    select_sql = ",\n            ".join(selects)
    return f"""
        SELECT
            {select_sql}
        FROM estimated_fill_bid_eligible b
        INNER JOIN estimated_fill_ask_eligible a
          ON a.market_id = b.market_id
         AND a.timestamp_ms = b.timestamp_ms
        GROUP BY b.market_id
        """


def _rate_or_null(samples: int, rate: float | None) -> float | None:
    if samples < MIN_MEANINGFUL_SAMPLES:
        return None
    if rate is None:
        return None
    return float(rate)


def _row_from_agg(rec: dict[str, Any]) -> dict[str, Any]:
    samples = int(rec.get("estimated_maker_fill_samples") or 0)
    detail: dict[str, Any] = {}
    for size in ORDER_SIZES_USD:
        size_i = int(size)
        size_key = str(size_i)
        detail[size_key] = {}
        for horizon in HORIZONS_S:
            detail[size_key][f"{horizon}s"] = {
                "optimistic": _rate_or_null(
                    samples, rec.get(f"mkt_optimistic_{horizon}s_{size_i}")
                ),
                "conservative": _rate_or_null(
                    samples, rec.get(f"mkt_conservative_{horizon}s_{size_i}")
                ),
                "bid_optimistic": _rate_or_null(
                    samples, rec.get(f"bid_optimistic_{horizon}s_{size_i}")
                ),
                "bid_conservative": _rate_or_null(
                    samples, rec.get(f"bid_conservative_{horizon}s_{size_i}")
                ),
                "ask_optimistic": _rate_or_null(
                    samples, rec.get(f"ask_optimistic_{horizon}s_{size_i}")
                ),
                "ask_conservative": _rate_or_null(
                    samples, rec.get(f"ask_conservative_{horizon}s_{size_i}")
                ),
            }

    default = detail[str(int(DEFAULT_ORDER_USD))]
    return {
        "estimated_maker_fill_samples": samples,
        "estimated_maker_fill_sample_quality": sample_quality(samples),
        "estimated_maker_fill_rate_5s_conservative": default["5s"]["conservative"],
        "estimated_maker_fill_rate_30s_conservative": default["30s"]["conservative"],
        "estimated_maker_fill_rate_5s_optimistic": default["5s"]["optimistic"],
        "estimated_maker_fill_rate_30s_optimistic": default["30s"]["optimistic"],
        "estimated_maker_fill_by_size": detail,
        "estimated_maker_edge_5s_bps": None,
        "estimated_maker_edge_30s_bps": None,
        "estimated_maker_edge_fee_included": False,
    }


def attach_estimated_maker_edge(
    row: dict[str, Any],
    *,
    maker_fee: float | None = None,
    maker_fee_bps: float | None = None,
) -> None:
    """Mutate row with Estimated Maker Edge using $50 conservative fill rates."""
    if maker_fee_bps is None and maker_fee is not None:
        fee_bps = maker_fee_rate_to_bps(maker_fee)
        fee_included = True
    elif maker_fee_bps is not None:
        fee_bps = float(maker_fee_bps)
        fee_included = True
    else:
        # Analyzer path has no MarketMeta fees today; exclude fee explicitly (0).
        fee_bps = 0.0
        fee_included = False

    row["estimated_maker_edge_fee_included"] = fee_included
    row["estimated_maker_edge_5s_bps"] = estimated_maker_edge_bps(
        fill_rate=row.get("estimated_maker_fill_rate_5s_conservative"),
        median_spread_bps=row.get("median_spread_bps"),
        maker_markout_bps=row.get("maker_markout_5s_median_bps"),
        maker_fee_bps=fee_bps,
    )
    row["estimated_maker_edge_30s_bps"] = estimated_maker_edge_bps(
        fill_rate=row.get("estimated_maker_fill_rate_30s_conservative"),
        median_spread_bps=row.get("median_spread_bps"),
        maker_markout_bps=row.get("maker_markout_30s_median_bps"),
        maker_fee_bps=fee_bps,
    )


def _trade_deduped_ready(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        cols = {r[0] for r in con.execute("DESCRIBE trade_deduped").fetchall()}
    except duckdb.CatalogException:
        return False
    required = {"market_id", "timestamp_ms", "price", "usd_amount", "is_maker_ask"}
    return required.issubset(cols)


def _book_observed_ready(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        con.execute("SELECT 1 FROM book_observed LIMIT 1")
        return True
    except duckdb.CatalogException:
        return False


def aggregate_estimated_fill_sql(
    con: duckdb.DuckDBPyConnection,
    *,
    bucket_ms: int = SNAPSHOT_BUCKET_MS,
) -> dict[int, dict[str, Any]]:
    """Compute Estimated Maker Fill rates from materialized books/trades."""
    if not _book_observed_ready(con) or not _trade_deduped_ready(con):
        return {}

    con.execute(_downsample_snapshots_sql(bucket_ms))
    snap_count = con.execute("SELECT COUNT(*) FROM estimated_fill_snapshots").fetchone()[0]
    if not snap_count:
        return {}

    con.execute(_side_eligible_sql("bid"))
    con.execute(_side_eligible_sql("ask"))
    rows = con.execute(_aggregate_fill_rates_sql()).fetchall()
    cols = [d[0] for d in con.description]
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        rec = dict(zip(cols, row, strict=True))
        mid = int(rec["market_id"])
        out[mid] = _row_from_agg(rec)
    return out


def _side_eligible_select_sql(side: str, horizon_ms: int = 30_000) -> str:
    """SELECT body for side-eligible aggregation (shared by CREATE / EXPLAIN)."""
    create_sql = _side_eligible_sql(side, horizon_ms=horizon_ms)
    marker = f"CREATE OR REPLACE TABLE estimated_fill_{side}_eligible AS"
    if marker not in create_sql:
        raise RuntimeError(f"unexpected eligible SQL shape for side={side}")
    return create_sql.split(marker, 1)[1].strip()


def estimated_fill_explain_plans(
    con: duckdb.DuckDBPyConnection,
    *,
    bucket_ms: int = SNAPSHOT_BUCKET_MS,
) -> dict[str, str]:
    """EXPLAIN ANALYZE for estimated-fill range joins (no unbounded CROSS_PRODUCT)."""
    if not _book_observed_ready(con) or not _trade_deduped_ready(con):
        return {}
    con.execute(_downsample_snapshots_sql(bucket_ms))
    plans: dict[str, str] = {}
    for side in ("bid", "ask"):
        select_sql = _side_eligible_select_sql(side)
        rows = con.execute(f"EXPLAIN ANALYZE {select_sql}").fetchall()
        plans[side] = "\n".join(str(r[1] if len(r) > 1 else r[0]) for r in rows)
    return plans


def downsample_snapshot_counts(
    con: duckdb.DuckDBPyConnection,
    *,
    bucket_ms: int = SNAPSHOT_BUCKET_MS,
) -> dict[int, int]:
    """Return per-market downsampled snapshot counts (for tests / diagnostics)."""
    if not _book_observed_ready(con):
        return {}
    con.execute(_downsample_snapshots_sql(bucket_ms))
    rows = con.execute(
        """
        SELECT market_id, COUNT(*)::BIGINT AS n
        FROM estimated_fill_snapshots
        GROUP BY market_id
        """
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}
