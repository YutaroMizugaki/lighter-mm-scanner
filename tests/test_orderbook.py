"""Order book snapshot/delta/nonce/resync tests with mock WS payloads."""

from __future__ import annotations

from lighter_mm.orderbook.book import LocalOrderBook


def test_snapshot_apply() -> None:
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 100,
            "begin_nonce": 0,
            "asks": [{"price": "2000.0", "size": "1.5"}],
            "bids": [{"price": "1999.0", "size": "2.0"}],
        }
    )
    assert book.synced
    assert book.best_bid()[0] == __import__("decimal").Decimal("1999.0")
    assert book.best_ask()[0] == __import__("decimal").Decimal("2000.0")
    assert book.mid() == __import__("decimal").Decimal("1999.5")


def test_delta_apply_and_delete() -> None:
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 100,
            "begin_nonce": 0,
            "asks": [{"price": "2000.0", "size": "1.5"}, {"price": "2001.0", "size": "3"}],
            "bids": [{"price": "1999.0", "size": "2.0"}],
        }
    )
    ok = book.apply_delta(
        {
            "begin_nonce": 100,
            "nonce": 110,
            "asks": [{"price": "2000.0", "size": "0"}],  # delete
            "bids": [{"price": "1998.5", "size": "4"}],
        }
    )
    assert ok
    assert book.nonce == 110
    assert __import__("decimal").Decimal("2000.0") not in book.asks
    assert book.best_ask()[0] == __import__("decimal").Decimal("2001.0")
    assert book.best_bid()[0] == __import__("decimal").Decimal("1999.0")


def test_nonce_gap_triggers_false() -> None:
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {"nonce": 100, "begin_nonce": 0, "asks": [{"price": "1", "size": "1"}], "bids": []}
    )
    ok = book.apply_delta(
        {"begin_nonce": 999, "nonce": 1000, "asks": [{"price": "1", "size": "2"}], "bids": []}
    )
    assert ok is False
    assert book.nonce_gap_count == 1
    assert book.synced is False


def test_resync_clears_book() -> None:
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "asks": [{"price": "10", "size": "1"}],
            "bids": [{"price": "9", "size": "1"}],
        }
    )
    book.mark_resync()
    assert book.resync_count == 1
    assert not book.bids and not book.asks
    assert book.synced is False


def test_spread_and_depth() -> None:
    book = LocalOrderBook(market_id=0, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [
                {"price": "100.00", "size": "10"},  # $1000
                {"price": "99.90", "size": "10"},  # within 10bp of mid~100.05? 
            ],
            "asks": [
                {"price": "100.10", "size": "10"},
                {"price": "100.20", "size": "10"},
            ],
        },
        recv_ms=10_000,
    )
    m = book.compute_metrics(depth_bps_levels=[5, 10, 25, 50, 100], stale_seconds=30, now_ms=10_500)
    assert m.spread_bps is not None
    assert m.spread_bps > 0
    assert m.depths["two_sided_depth_10bps_usd"] > 0
    assert m.depths["two_sided_depth_10bps_usd"] == min(
        m.depths["bid_depth_10bps_usd"], m.depths["ask_depth_10bps_usd"]
    )
