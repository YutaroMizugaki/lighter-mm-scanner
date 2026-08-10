"""Estimated Maker Fill — public analytics API.

Uses already-materialized usable book snapshots and regular trades.
This is NOT actual fill probability: queue position, cancels/amends, and
own-order lifecycle are not observed.

Policy constants live in ``estimated_fill_policy``; SQL builders in
``estimated_fill_sql``. Constants are re-exported here for compatibility.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

from lighter_mm.analytics.estimated_fill_policy import (
    DEFAULT_ORDER_USD,
    HORIZONS_S,
    MIN_MEANINGFUL_SAMPLES,
    ORDER_SIZES_USD,
    PRELIMINARY_SAMPLES,
    SNAPSHOT_BUCKET_MS,
)
from lighter_mm.analytics.estimated_fill_sql import (
    aggregate_fill_rates_sql,
    downsample_snapshots_sql,
    side_eligible_select_sql,
    side_eligible_sql,
)

# Re-export policy constants for existing imports.
__all__ = [
    "DEFAULT_ORDER_USD",
    "HORIZONS_S",
    "MIN_MEANINGFUL_SAMPLES",
    "ORDER_SIZES_USD",
    "PRELIMINARY_SAMPLES",
    "SNAPSHOT_BUCKET_MS",
    "aggregate_estimated_fill_sql",
    "attach_estimated_maker_edge",
    "downsample_snapshot_counts",
    "empty_estimated_fill_stats",
    "estimated_fill_explain_plans",
    "estimated_maker_edge_bps",
    "maker_fee_rate_to_bps",
    "max_estimated_fill_snapshots",
    "sample_quality",
    "snapshot_bucket_id",
]


def sample_quality(n: int | None) -> str:
    """Classify observation count for Estimated Fill / markout reliability labels."""
    count = int(n or 0)
    if count < MIN_MEANINGFUL_SAMPLES:
        return "insufficient"
    if count < PRELIMINARY_SAMPLES:
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
    maker_markout_bps: float | None,
    maker_fee_bps: float = 0.0,
) -> float | None:
    """fill_rate × (maker_markout − fee). Not expected profit.

    Maker markout is already measured from the executed maker price to a future
    mid, so entry spread economics are included there. Do **not** add half-spread
    again (that would double-count spread).
    """
    if fill_rate is None or maker_markout_bps is None:
        return None
    return float(fill_rate) * (float(maker_markout_bps) - float(maker_fee_bps))


def snapshot_bucket_id(timestamp_ms: int, bucket_ms: int = SNAPSHOT_BUCKET_MS) -> int:
    """Floor bucket id: ``floor(timestamp_ms / bucket_ms)``."""
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be > 0")
    return int(timestamp_ms) // int(bucket_ms)


def max_estimated_fill_snapshots(window_hours: float, bucket_ms: int = SNAPSHOT_BUCKET_MS) -> int:
    """Upper bound on downsampled snapshots for one market over an analysis window."""
    if window_hours <= 0:
        return 0
    window_ms = window_hours * 3_600_000.0
    return int(math.ceil(window_ms / float(bucket_ms)))


def empty_estimated_fill_stats() -> dict[str, Any]:
    """Null/zero Estimated Fill fields for markets without a successful aggregation."""
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
        maker_markout_bps=row.get("maker_markout_5s_median_bps"),
        maker_fee_bps=fee_bps,
    )
    row["estimated_maker_edge_30s_bps"] = estimated_maker_edge_bps(
        fill_rate=row.get("estimated_maker_fill_rate_30s_conservative"),
        maker_markout_bps=row.get("maker_markout_30s_median_bps"),
        maker_fee_bps=fee_bps,
    )


def _trade_fields_computable(con: duckdb.DuckDBPyConnection) -> bool:
    """True only when trade_deduped can support Estimated Fill.

    Column names alone are insufficient: aggregation may project missing source
    fields as all-NULL ``price`` / ``is_maker_ask``. That must be treated as
    unavailable, not as measured 0% fill.
    """
    try:
        cols = {str(r[0]) for r in con.execute("DESCRIBE trade_deduped").fetchall()}
    except duckdb.CatalogException:
        return False
    required = {"market_id", "timestamp_ms", "price", "usd_amount", "is_maker_ask"}
    if not required.issubset(cols):
        return False
    n, n_price, n_side = con.execute(
        """
        SELECT
            COUNT(*)::BIGINT,
            COUNT(price)::BIGINT,
            COUNT(is_maker_ask)::BIGINT
        FROM trade_deduped
        """
    ).fetchone()
    # Empty regular-trade table with a valid schema is computable (measured zero).
    if int(n) == 0:
        return True
    # All-null synthetic columns ⇒ source fields unavailable.
    return int(n_price) > 0 and int(n_side) > 0


def _trade_deduped_ready(con: duckdb.DuckDBPyConnection) -> bool:
    return _trade_fields_computable(con)


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
    source_fields_available: bool | None = None,
) -> dict[int, dict[str, Any]]:
    """Compute Estimated Maker Fill rates from materialized books/trades.

    ``source_fields_available=False`` short-circuits when the caller already
    knows required raw trade columns were missing from Parquet (schema drift).
    When omitted, readiness is inferred from ``trade_deduped`` contents.
    """
    if source_fields_available is False:
        return {}
    if not _book_observed_ready(con) or not _trade_deduped_ready(con):
        return {}

    con.execute(downsample_snapshots_sql(bucket_ms))
    snap_count = con.execute("SELECT COUNT(*) FROM estimated_fill_snapshots").fetchone()[0]
    if not snap_count:
        return {}

    con.execute(side_eligible_sql("bid"))
    con.execute(side_eligible_sql("ask"))
    rows = con.execute(aggregate_fill_rates_sql()).fetchall()
    cols = [d[0] for d in con.description]
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        rec = dict(zip(cols, row, strict=True))
        mid = int(rec["market_id"])
        out[mid] = _row_from_agg(rec)
    return out


def estimated_fill_explain_plans(
    con: duckdb.DuckDBPyConnection,
    *,
    bucket_ms: int = SNAPSHOT_BUCKET_MS,
) -> dict[str, str]:
    """EXPLAIN ANALYZE for estimated-fill range joins (no unbounded CROSS_PRODUCT)."""
    if not _book_observed_ready(con) or not _trade_deduped_ready(con):
        return {}
    con.execute(downsample_snapshots_sql(bucket_ms))
    plans: dict[str, str] = {}
    for side in ("bid", "ask"):
        select_sql = side_eligible_select_sql(side)
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
    con.execute(downsample_snapshots_sql(bucket_ms))
    rows = con.execute(
        """
        SELECT market_id, COUNT(*)::BIGINT AS n
        FROM estimated_fill_snapshots
        GROUP BY market_id
        """
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}
