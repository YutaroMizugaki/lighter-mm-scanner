"""DuckDB SQL builders for Estimated Maker Fill (private helpers).

SQL semantics must stay identical to Estimated Maker Fill v1:
- floor bucket: ``timestamp_ms // SNAPSHOT_BUCKET_MS``
- virtual maker: buy @ Best Bid, sell @ Best Ask
- eligible: bid side ``is_maker_ask = false AND price <= best_bid``;
  ask side ``is_maker_ask = true AND price >= best_ask``
- optimistic: eligible notional >= order size
- conservative: eligible notional >= displayed touch size + order size
- market-level: min(bid_rate, ask_rate)
"""

from __future__ import annotations

from lighter_mm.analytics.estimated_fill_policy import (
    ORDER_SIZES_USD,
    SNAPSHOT_BUCKET_MS,
)


def downsample_snapshots_sql(bucket_ms: int = SNAPSHOT_BUCKET_MS) -> str:
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
                    PARTITION BY market_id, (timestamp_ms // {bucket_ms})
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


def side_eligible_sql(side: str, horizon_ms: int = 30_000) -> str:
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


def fill_flag_expr(eligible_col: str, touch_size_col: str, order_usd: float, mode: str) -> str:
    if mode == "optimistic":
        required = f"{order_usd}"
    elif mode == "conservative":
        required = f"({touch_size_col} + {order_usd})"
    else:
        raise ValueError(mode)
    return f"CASE WHEN {eligible_col} >= {required} THEN 1.0 ELSE 0.0 END"


def aggregate_fill_rates_sql() -> str:
    """Market-level fill rates: min(bid_rate, ask_rate) per size/horizon/mode."""
    selects: list[str] = [
        "b.market_id",
        "COUNT(*)::BIGINT AS estimated_maker_fill_samples",
    ]
    for size in ORDER_SIZES_USD:
        size_i = int(size)
        for horizon, elig in ((5, "eligible_usd_5s"), (30, "eligible_usd_30s")):
            for mode in ("optimistic", "conservative"):
                bid_flag = fill_flag_expr(f"b.{elig}", "b.touch_size_usd", size, mode)
                ask_flag = fill_flag_expr(f"a.{elig}", "a.touch_size_usd", size, mode)
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


def side_eligible_select_sql(side: str, horizon_ms: int = 30_000) -> str:
    """SELECT body for side-eligible aggregation (shared by CREATE / EXPLAIN)."""
    create_sql = side_eligible_sql(side, horizon_ms=horizon_ms)
    marker = f"CREATE OR REPLACE TABLE estimated_fill_{side}_eligible AS"
    if marker not in create_sql:
        raise RuntimeError(f"unexpected eligible SQL shape for side={side}")
    return create_sql.split(marker, 1)[1].strip()
