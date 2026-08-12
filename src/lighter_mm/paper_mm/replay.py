"""DuckDB event stream replay for Paper MM."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import duckdb

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.engine import finalize_state, on_book, on_trade
from lighter_mm.paper_mm.models import BookSnapshot, PaperMmConfig, PaperMmState, TradeEvent

_EVENT_SQL = """
SELECT
    timestamp_ms,
    0 AS event_priority,
    'trade' AS event_type,
    trade_id,
    price,
    usd_amount,
    is_maker_ask,
    CAST(NULL AS DOUBLE) AS best_bid,
    CAST(NULL AS DOUBLE) AS best_ask,
    CAST(NULL AS DOUBLE) AS best_bid_size_usd,
    CAST(NULL AS DOUBLE) AS best_ask_size_usd,
    CAST(NULL AS DOUBLE) AS mid
FROM trade_deduped
WHERE market_id = ?
  AND timestamp_ms >= ?
  AND timestamp_ms <= ?

UNION ALL

SELECT
    timestamp_ms,
    1 AS event_priority,
    'book' AS event_type,
    CAST(NULL AS BIGINT) AS trade_id,
    CAST(NULL AS DOUBLE) AS price,
    CAST(NULL AS DOUBLE) AS usd_amount,
    CAST(NULL AS BOOLEAN) AS is_maker_ask,
    best_bid,
    best_ask,
    best_bid_size_usd,
    best_ask_size_usd,
    mid
FROM book_observed
WHERE market_id = ?
  AND timestamp_ms >= ?
  AND timestamp_ms <= ?
  AND best_bid IS NOT NULL
  AND best_ask IS NOT NULL

ORDER BY timestamp_ms, event_priority, trade_id
"""


def iter_market_events(
    con: duckdb.DuckDBPyConnection,
    market_id: int,
    start_ms: int,
    end_ms: int,
    *,
    batch_size: int = 5000,
) -> Iterator[tuple]:
    cur = con.execute(
        _EVENT_SQL,
        [market_id, start_ms, end_ms, market_id, start_ms, end_ms],
    )
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        yield from rows


def run_paper_mm_replay(
    con: duckdb.DuckDBPyConnection,
    market_id: int,
    start_ms: int,
    end_ms: int,
    config: PaperMmConfig,
    window_hours: float,
) -> dict[str, Any]:
    state = PaperMmState()
    mid_hist = MidHistory(retention_seconds=max(300.0, config.max_quote_age_seconds * 4))

    for row in iter_market_events(con, market_id, start_ms, end_ms):
        (
            ts_ms,
            _prio,
            event_type,
            trade_id,
            price,
            usd_amount,
            is_maker_ask,
            best_bid,
            best_ask,
            best_bid_size_usd,
            best_ask_size_usd,
            mid,
        ) = row

        if event_type == "trade":
            if is_maker_ask is None or price is None or usd_amount is None:
                continue
            trade = TradeEvent(
                timestamp_ms=int(ts_ms),
                trade_id=int(trade_id or 0),
                price=float(price),
                usd_amount=float(usd_amount),
                is_maker_ask=bool(is_maker_ask),
            )
            on_trade(state, config, trade, mid_hist)
        else:
            book = BookSnapshot(
                timestamp_ms=int(ts_ms),
                best_bid=float(best_bid),
                best_ask=float(best_ask),
                best_bid_size_usd=float(best_bid_size_usd or 0.0),
                best_ask_size_usd=float(best_ask_size_usd or 0.0),
                mid=float(mid or 0.0),
            )
            on_book(state, config, book, mid_hist)

    return finalize_state(state, config, mid_hist, end_ms, window_hours)
