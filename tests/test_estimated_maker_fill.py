"""Gate A/B tests for Estimated Maker Fill semantics and performance structure."""

from __future__ import annotations

import duckdb
import pytest

from lighter_mm.analytics.estimated_fill_metrics import (
    DEFAULT_ORDER_USD,
    MIN_MEANINGFUL_SAMPLES,
    SNAPSHOT_BUCKET_MS,
    aggregate_estimated_fill_sql,
    attach_estimated_maker_edge,
    downsample_snapshot_counts,
    estimated_fill_explain_plans,
    estimated_maker_edge_bps,
    maker_fee_rate_to_bps,
    max_estimated_fill_snapshots,
    sample_quality,
)


def _setup_books_trades(
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


def _single_snap_books(
    *,
    bid: float = 100.0,
    ask: float = 101.0,
    bid_size: float = 100.0,
    ask_size: float = 100.0,
    usable: bool = True,
    ts: int = 1_000_000,
) -> list[tuple]:
    mid = (bid + ask) / 2.0
    return [(1, "TEST", ts, mid, usable, bid, ask, bid_size, ask_size)]


def test_buy_conservative_true() -> None:
    con = duckdb.connect()
    # required = 100 + 50 = 150; eligible taker-sell = 160 → true
    # Also seed ask-side flow so market-level min(bid, ask) stays high.
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        trades.append((1, i * 2 + 1, ts + 1000, 100.0, 160.0, False, "trade"))
        trades.append((1, i * 2 + 2, ts + 1000, 101.0, 160.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_samples"] >= MIN_MEANINGFUL_SAMPLES
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_conservative"] == pytest.approx(
        1.0
    )
    assert out["estimated_maker_fill_rate_5s_conservative"] == pytest.approx(1.0)


def test_buy_conservative_false_optimistic_true() -> None:
    con = duckdb.connect()
    # eligible = 100: conservative needs 150 → false; optimistic needs 50 → true
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        trades.append((1, i + 1, ts + 1000, 100.0, 100.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_conservative"] == pytest.approx(
        0.0
    )
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        1.0
    )


def test_buy_optimistic_at_exact_order_size() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        trades.append((1, i + 1, ts + 1000, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        1.0
    )
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_conservative"] == pytest.approx(
        0.0
    )


def test_sell_conservative_true() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        # taker buy eligible @ ask
        trades.append((1, i + 1, ts + 1000, 101.0, 160.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["ask_conservative"] == pytest.approx(
        1.0
    )


def test_direction_taker_buy_excluded_from_buy_maker() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        # is_maker_ask=true → taker buy; must NOT fill bid maker
        trades.append((1, i + 1, ts + 1000, 100.0, 1000.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        0.0
    )


def test_direction_taker_sell_excluded_from_sell_maker() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        trades.append((1, i + 1, ts + 1000, 101.0, 1000.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["ask_optimistic"] == pytest.approx(
        0.0
    )


def test_price_filters() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        # Buy: trade_price > best_bid → excluded
        trades.append((1, i * 2 + 1, ts + 1000, 100.01, 1000.0, False, "trade"))
        # Sell: trade_price < best_ask → excluded
        trades.append((1, i * 2 + 2, ts + 1000, 100.99, 1000.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        0.0
    )
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["ask_optimistic"] == pytest.approx(
        0.0
    )


def test_horizon_boundaries() -> None:
    # Space snapshots by 120s so a trade near one horizon cannot fill the next snap.
    spacing = 120_000

    con = duckdb.connect()
    books = []
    trades = []
    # 4.9s → both 5s and 30s
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * spacing
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        trades.append((1, i + 1, ts + 4900, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out_early = aggregate_estimated_fill_sql(con)[1]
    assert out_early["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        1.0
    )
    assert out_early["estimated_maker_fill_by_size"]["50"]["30s"][
        "bid_optimistic"
    ] == pytest.approx(1.0)

    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * spacing
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        trades.append((1, i + 1, ts + 5100, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out_mid = aggregate_estimated_fill_sql(con)[1]
    assert out_mid["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        0.0
    )
    assert out_mid["estimated_maker_fill_by_size"]["50"]["30s"]["bid_optimistic"] == pytest.approx(
        1.0
    )

    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * spacing
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        trades.append((1, i + 1, ts + 30100, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out_late = aggregate_estimated_fill_sql(con)[1]
    assert out_late["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        0.0
    )
    assert out_late["estimated_maker_fill_by_size"]["50"]["30s"][
        "bid_optimistic"
    ] == pytest.approx(0.0)


def test_timestamp_equality_semantics() -> None:
    """trade.timestamp_ms > t and <= t+horizon; equality at snapshot excluded."""
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        # exactly at snapshot → excluded
        trades.append((1, i * 2 + 1, ts, 100.0, 50.0, False, "trade"))
        # exactly at t+5s → included in 5s
        trades.append((1, i * 2 + 2, ts + 5000, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        1.0
    )


def test_regular_trade_only_non_regular_excluded() -> None:
    """trade_deduped is already regular-only; liquidations must not be present.

    Guard: if a non-regular row were inserted into the source table used for
    fill (should not happen in production), aggregation still requires the
    production trade_deduped view which filters type='trade'. Here we assert
    that only rows in trade_deduped participate — liquidations omitted from
    the table do not create fills.
    """
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        # No regular trades inserted → fill rate 0 with enough samples
    _setup_books_trades(con, books=books, trades=trades)
    # Simulate liquidations sitting only in trade_raw (not trade_deduped)
    con.execute(
        """
        CREATE TABLE trade_raw AS
        SELECT * FROM trade_deduped
        """
    )
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        con.execute(
            """
            INSERT INTO trade_raw
            VALUES (1, ?, ?, 100.0, 1000.0, false, 'liquidation')
            """,
            [10_000 + i, ts + 1000],
        )
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_rate_5s_optimistic"] == pytest.approx(0.0)


def test_usable_book_and_missing_bbo_excluded() -> None:
    con = duckdb.connect()
    books = [
        # unusable
        (1, "TEST", 1_000_000, 100.5, False, 100.0, 101.0, 100.0, 100.0),
        # missing BBO
        (1, "TEST", 1_000_000 + SNAPSHOT_BUCKET_MS, 100.5, True, None, 101.0, 100.0, 100.0),
        (1, "TEST", 1_000_000 + 2 * SNAPSHOT_BUCKET_MS, 100.5, True, 100.0, None, 100.0, 100.0),
    ]
    _setup_books_trades(con, books=books, trades=[])
    assert downsample_snapshot_counts(con) == {}
    assert aggregate_estimated_fill_sql(con) == {}


def test_sample_quality_boundaries() -> None:
    assert sample_quality(0) == "insufficient"
    assert sample_quality(99) == "insufficient"
    assert sample_quality(100) == "preliminary"
    assert sample_quality(499) == "preliminary"
    assert sample_quality(500) == "reliable"


def test_null_vs_zero_fill_semantics() -> None:
    con = duckdb.connect()
    # Insufficient samples with zero eligible volume → null rates
    books = [(1, "TEST", 1_000_000, 100.5, True, 100.0, 101.0, 100.0, 100.0)]
    _setup_books_trades(con, books=books, trades=[])
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_samples"] == 1
    assert out["estimated_maker_fill_sample_quality"] == "insufficient"
    assert out["estimated_maker_fill_rate_30s_conservative"] is None

    # Enough samples, no eligible flow → explicit 0
    con = duckdb.connect()
    books = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
    _setup_books_trades(con, books=books, trades=[])
    out0 = aggregate_estimated_fill_sql(con)[1]
    assert out0["estimated_maker_fill_samples"] == MIN_MEANINGFUL_SAMPLES
    assert out0["estimated_maker_fill_rate_30s_conservative"] == pytest.approx(0.0)


def test_market_level_uses_min_of_bid_ask() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        # Only bid side fills
        trades.append((1, i + 1, ts + 1000, 100.0, 50.0, False, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["bid_optimistic"] == pytest.approx(
        1.0
    )
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["ask_optimistic"] == pytest.approx(
        0.0
    )
    assert out["estimated_maker_fill_rate_5s_optimistic"] == pytest.approx(0.0)


def test_size_ladder_25_50_100() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 0.0, 0.0))
        # $60 eligible both sides → fills 25 and 50, not 100 (optimistic)
        trades.append((1, i * 2 + 1, ts + 1000, 100.0, 60.0, False, "trade"))
        trades.append((1, i * 2 + 2, ts + 1000, 101.0, 60.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_by_size"]["25"]["5s"]["optimistic"] == pytest.approx(1.0)
    assert out["estimated_maker_fill_by_size"]["50"]["5s"]["optimistic"] == pytest.approx(1.0)
    assert out["estimated_maker_fill_by_size"]["100"]["5s"]["optimistic"] == pytest.approx(0.0)
    assert DEFAULT_ORDER_USD == 50.0


def test_edge_markout_minus_fee_formula() -> None:
    # markout=+1; fee=0; fill=0.5 → edge=0.5 (no half-spread term)
    edge = estimated_maker_edge_bps(
        fill_rate=0.5,
        maker_markout_bps=1.0,
        maker_fee_bps=0.0,
    )
    assert edge == pytest.approx(0.5)

    row = {
        "median_spread_bps": 4.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 1.0,
        "estimated_maker_fill_rate_5s_conservative": 0.5,
        "estimated_maker_fill_rate_30s_conservative": 0.5,
    }
    attach_estimated_maker_edge(row)
    assert row["estimated_maker_edge_5s_bps"] == pytest.approx(0.5)
    assert row["estimated_maker_edge_fee_included"] is False


def test_maker_fee_rate_to_bps() -> None:
    assert maker_fee_rate_to_bps(0.00010) == pytest.approx(1.0)
    assert maker_fee_rate_to_bps(0.00045) == pytest.approx(4.5)
    edge = estimated_maker_edge_bps(
        fill_rate=1.0,
        maker_markout_bps=1.0,
        maker_fee_bps=maker_fee_rate_to_bps(0.00010),
    )
    # markout 1 - fee 1 = 0
    assert edge == pytest.approx(0.0)


def test_downsample_caps_24h_window() -> None:
    con = duckdb.connect()
    # Dense 1s books over 24h would be huge; simulate 24h at 1s with 1 market
    # using a smaller representative: 1 hour at 1s + check formula bound.
    window_hours = 24.0
    bound = max_estimated_fill_snapshots(window_hours)
    assert bound == 2880

    books = []
    start = 0
    # 2 hours of 1s samples → raw 7200; downsampled <= ceil(2h/30s)=240
    for i in range(7200):
        ts = start + i * 1000
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 10.0, 10.0))
    _setup_books_trades(con, books=books, trades=[])
    counts = downsample_snapshot_counts(con)
    # ~ceil(window/30s); allow +1 for inclusive endpoint / integer-bucket edge.
    assert counts[1] <= max_estimated_fill_snapshots(2.0) + 1
    assert counts[1] >= 240
    assert counts[1] < len(books) // 10  # must not scale with 1s raw sampling


def test_explain_no_unconditional_cross_product() -> None:
    con = duckdb.connect()
    books = []
    trades = []
    for i in range(20):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        books.append((1, "TEST", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
        trades.append((1, i + 1, ts + 1000, 100.0, 50.0, False, "trade"))
        trades.append((1, 1000 + i, ts + 1000, 101.0, 50.0, True, "trade"))
    _setup_books_trades(con, books=books, trades=trades)
    plans = estimated_fill_explain_plans(con)
    assert "bid" in plans and "ask" in plans
    for side, plan in plans.items():
        upper = plan.upper()
        # Fail on clear unconditional cartesian strategies.
        assert "CROSS_PRODUCT" not in upper, f"{side} plan has CROSS_PRODUCT:\n{plan}"
