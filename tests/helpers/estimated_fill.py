"""Shared fixtures for Estimated Maker Fill tests."""

from __future__ import annotations

import duckdb

from lighter_mm.analytics.estimated_fill_policy import (
    MIN_MEANINGFUL_SAMPLES,
    SNAPSHOT_BUCKET_MS,
)


def setup_books_trades(
    con: duckdb.DuckDBPyConnection,
    *,
    books: list[tuple],
    trades: list[tuple],
) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE book_observed (
            market_id BIGINT,
            symbol VARCHAR,
            timestamp_ms BIGINT,
            mid DOUBLE,
            is_usable BOOLEAN,
            best_bid DOUBLE,
            best_ask DOUBLE,
            best_bid_size_usd DOUBLE,
            best_ask_size_usd DOUBLE
        )
        """
    )
    if books:
        con.executemany(
            "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            books,
        )
    con.execute(
        """
        CREATE OR REPLACE TABLE trade_deduped (
            market_id BIGINT,
            trade_id BIGINT,
            timestamp_ms BIGINT,
            price DOUBLE,
            usd_amount DOUBLE,
            is_maker_ask BOOLEAN,
            type VARCHAR
        )
        """
    )
    if trades:
        con.executemany(
            "INSERT INTO trade_deduped VALUES (?, ?, ?, ?, ?, ?, ?)",
            trades,
        )


def make_book_rows(
    n: int = MIN_MEANINGFUL_SAMPLES,
    *,
    market_id: int = 1,
    symbol: str = "T",
    bid: float = 100.0,
    ask: float = 101.0,
    bid_size: float = 100.0,
    ask_size: float = 100.0,
    usable: bool = True,
    start_ts: int = 1_000_000,
    spacing_ms: int = SNAPSHOT_BUCKET_MS,
) -> list[tuple]:
    mid = (bid + ask) / 2.0
    out: list[tuple] = []
    for i in range(n):
        ts = start_ts + i * spacing_ms
        out.append((market_id, symbol, ts, mid, usable, bid, ask, bid_size, ask_size))
    return out


def make_candidate_row(**overrides: object) -> dict:
    row: dict = {
        "symbol": "CAND",
        "market_id": 1,
        "observation_hours": 24.0,
        "data_coverage_pct": 98.0,
        "observation_coverage_pct": 98.0,
        "median_spread_bps": 5.0,
        "pct_time_spread_ge_5bps": 0.4,
        "median_two_sided_depth_10bps_usd": 2000.0,
        "median_two_sided_depth_5bps_usd": 1000.0,
        "trades_per_minute_median": 5.0,
        "trades_per_minute_mean": 5.0,
        "total_trade_count": 10_000,
        "maker_markout_5s_median_bps": 0.5,
        "maker_markout_30s_median_bps": 0.2,
        "markout_5s_count": 200,
        "markout_30s_count": 200,
        "estimated_maker_fill_samples": 500,
        "estimated_maker_fill_sample_quality": "reliable",
        "estimated_maker_fill_rate_30s_conservative": 0.4,
        "estimated_maker_fill_rate_5s_conservative": 0.2,
        "estimated_maker_fill_rate_30s_optimistic": 0.6,
        "estimated_maker_fill_rate_5s_optimistic": 0.3,
    }
    row.update(overrides)
    return row
