"""Characterization tests for pure collector sampling builders."""

from __future__ import annotations

from lighter_mm.orderbook.book import OrderBookMetrics
from lighter_mm.runtime.sampling import (
    build_book_sample_row,
    build_dq_update,
    build_live_metric,
)


def _sample_metrics() -> OrderBookMetrics:
    return OrderBookMetrics(
        timestamp_ms=1_700_000_000_000,
        market_id=42,
        symbol="BTC",
        best_bid=99.0,
        best_ask=101.0,
        mid=100.0,
        spread_absolute=2.0,
        spread_bps=20.0,
        best_bid_size_base=1.0,
        best_ask_size_base=1.0,
        best_bid_size_usd=99.0,
        best_ask_size_usd=101.0,
        depths={"two_sided_depth_10bps_usd": 50_000.0},
        is_stale=False,
        is_usable=True,
        is_inactive=False,
        book_update_age_ms=100,
        nonce=7,
    )


def test_build_book_sample_row_shape():
    row = build_book_sample_row(
        market_id=42,
        symbol="BTC",
        metrics=_sample_metrics(),
        stats=None,
    )
    assert row["market_id"] == 42
    assert row["symbol"] == "BTC"
    assert row["is_usable"] is True
    assert row["two_sided_depth_10bps_usd"] == 50_000.0
    assert row["index_price"] is None


def test_build_live_metric_shape():
    metric = build_live_metric(
        symbol="BTC",
        metrics=_sample_metrics(),
        tpm=12.5,
        markout_5s=1.2,
    )
    assert metric["symbol"] == "BTC"
    assert metric["tpm"] == 12.5
    assert metric["markout_5s"] == 1.2
    assert metric["depth_10bps"] == 50_000.0


def test_build_dq_update_shape():
    from lighter_mm.orderbook.book import LocalOrderBook

    book = LocalOrderBook(market_id=42, symbol="BTC")
    book.resync_count = 2
    book.nonce_gap_count = 1
    book.stale_count = 3
    mid, payload = build_dq_update(42, actual_samples=10, book=book)
    assert mid == 42
    assert payload == {
        "actual_samples": 10,
        "book_resync_count": 2,
        "nonce_gap_count": 1,
        "stale_book_count": 3,
    }
