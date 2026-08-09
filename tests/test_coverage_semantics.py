"""Coverage semantics: data coverage vs book inactivity vs WS health."""

from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.config import Settings
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.scoring import CandidateThresholds, score_markets
from lighter_mm.storage.parquet_store import ParquetStore
from tests.helpers import enrich_book_row


def _legacy_book_row(
    ts: int,
    *,
    stale: bool = False,
    mid: float | None = 100.05,
    market_id: int = 1,
    spread_bps: float | None = 10.0,
) -> dict:
    """Old-schema row without is_usable / book_update_age_ms."""
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": "ETH",
        "best_bid": (mid - 0.05) if mid is not None else None,
        "best_ask": (mid + 0.05) if mid is not None else None,
        "mid": mid,
        "spread_absolute": 0.1 if mid is not None else None,
        "spread_bps": None if stale else spread_bps,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 100.0,
        "best_ask_size_usd": 100.1,
        "is_stale": stale,
        "nonce": 1,
        "index_price": None,
        "mark_price": None,
        "stats_mid_price": None,
        "open_interest": None,
        "last_trade_price": None,
        "current_funding_rate": None,
        "funding_rate": None,
        "daily_base_token_volume": None,
        "daily_quote_token_volume": None,
        "daily_price_low": None,
        "daily_price_high": None,
        "daily_price_change": None,
        "bid_depth_5bps_usd": 0.0 if stale else 50.0,
        "ask_depth_5bps_usd": 0.0 if stale else 50.0,
        "two_sided_depth_5bps_usd": 0.0 if stale else 50.0,
        "bid_depth_10bps_usd": 0.0 if stale else 200.0,
        "ask_depth_10bps_usd": 0.0 if stale else 200.0,
        "two_sided_depth_10bps_usd": 0.0 if stale else 200.0,
        "bid_depth_25bps_usd": 0.0 if stale else 400.0,
        "ask_depth_25bps_usd": 0.0 if stale else 400.0,
        "two_sided_depth_25bps_usd": 0.0 if stale else 400.0,
    }


def _new_book_row(ts: int, book: LocalOrderBook, *, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    m = book.compute_metrics(
        depth_bps_levels=settings.depth_bps_levels,
        stale_seconds=settings.stale_book_seconds,
        now_ms=ts,
    )
    row = {
        "timestamp_ms": m.timestamp_ms,
        "market_id": book.market_id,
        "symbol": book.symbol,
        "best_bid": m.best_bid,
        "best_ask": m.best_ask,
        "mid": m.mid,
        "spread_absolute": m.spread_absolute,
        "spread_bps": m.spread_bps,
        "best_bid_size_base": m.best_bid_size_base,
        "best_ask_size_base": m.best_ask_size_base,
        "best_bid_size_usd": m.best_bid_size_usd,
        "best_ask_size_usd": m.best_ask_size_usd,
        "is_stale": m.is_stale,
        "is_usable": m.is_usable,
        "is_inactive": m.is_inactive,
        "book_update_age_ms": m.book_update_age_ms,
        "nonce": m.nonce,
        "index_price": None,
        "mark_price": None,
        "stats_mid_price": None,
        "open_interest": None,
        "last_trade_price": None,
        "current_funding_rate": None,
        "funding_rate": None,
        "daily_base_token_volume": None,
        "daily_quote_token_volume": None,
        "daily_price_low": None,
        "daily_price_high": None,
        "daily_price_change": None,
    }
    row.update(m.depths)
    return row


def _write_legacy_parquet(tmp_path: Path, rows: list[dict]) -> None:
    """Write old-schema parquet (no is_usable / activity columns)."""
    if not rows:
        return
    keys = list(rows[0].keys())
    data = {k: [r.get(k) for r in rows] for k in keys}
    out_dir = tmp_path / "book_samples" / "date=2026-08-09" / "hour=10"
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(data), out_dir / "legacy.parquet")


# TEST A — quiet synced book
def test_quiet_synced_book_is_usable_with_metrics() -> None:
    now = int(time.time() * 1000)
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [{"price": "100.00", "size": "10.0"}],
            "asks": [{"price": "100.10", "size": "10.0"}],
        },
        recv_ms=now - 600_000,
    )
    m = book.compute_metrics(depth_bps_levels=[5, 10], stale_seconds=180, now_ms=now)
    assert m.is_usable
    assert m.is_inactive
    assert m.mid is not None
    assert m.spread_bps is not None
    assert m.depths["two_sided_depth_10bps_usd"] > 0


# TEST B — disconnected book
def test_disconnected_book_not_usable() -> None:
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [{"price": "100.0", "size": "1.0"}],
            "asks": [{"price": "101.0", "size": "1.0"}],
        }
    )
    book.mark_resync()
    m = book.compute_metrics(depth_bps_levels=[5, 10], stale_seconds=180)
    assert not m.is_usable
    assert m.mid is None


# TEST C — nonce gap
def test_nonce_gap_makes_book_unusable() -> None:
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 100,
            "begin_nonce": 0,
            "bids": [{"price": "100.0", "size": "1.0"}],
            "asks": [{"price": "101.0", "size": "1.0"}],
        }
    )
    ok = book.apply_delta(
        {"begin_nonce": 999, "nonce": 1000, "asks": [{"price": "101.0", "size": "2.0"}], "bids": []}
    )
    assert ok is False
    assert not book.synced
    m = book.compute_metrics(depth_bps_levels=[5, 10], stale_seconds=180)
    assert not m.is_usable


# TEST D — quiet market coverage ~100%
def test_quiet_market_coverage_near_full(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=500, flush_seconds=60)
    base = int(time.time() * 1000) - 3_600_000
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [{"price": "100.0", "size": "10.0"}],
            "asks": [{"price": "100.1", "size": "10.0"}],
        },
        recv_ms=base,
    )
    for i in range(720):
        ts = base + i * 5000
        store.write_book(_new_book_row(ts, book, settings=settings))
    store.close()
    result = analyze_range(settings, start_ms=base, end_ms=base + 3_600_000)
    cov = result["markets"][0]["data_coverage_pct"]
    assert cov >= 99.0


# TEST E — real collection gap lowers coverage
def test_collection_gap_lowers_coverage(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=500, flush_seconds=60)
    base = int(time.time() * 1000) - 3_600_000
    gap_start = base + 1_800_000
    gap_end = gap_start + 600_000
    for i in range(720):
        ts = base + i * 5000
        if gap_start <= ts < gap_end:
            continue
        store.write_book(enrich_book_row(_legacy_book_row(ts, stale=False)))
    store.close()
    result = analyze_range(settings, start_ms=base, end_ms=base + 3_600_000)
    cov = result["markets"][0]["data_coverage_pct"]
    assert cov < 90.0


# TEST F — legacy stale rows with mid count toward coverage
def test_legacy_stale_with_mid_in_coverage(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    ts = int(time.time() * 1000)
    _write_legacy_parquet(tmp_path, [_legacy_book_row(ts, stale=True, mid=100.05)])
    result = analyze_range(settings, start_ms=ts - 1000, end_ms=ts + 1000)
    assert result["markets"][0]["observed_samples"] == 1
    assert result["markets"][0]["data_coverage_pct"] >= 80.0


# TEST G — disconnected rows still observed but not usable
def test_legacy_stale_without_mid_excluded(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    ts = int(time.time() * 1000)
    _write_legacy_parquet(tmp_path, [_legacy_book_row(ts, stale=True, mid=None)])
    result = analyze_range(settings, start_ms=ts - 1000, end_ms=ts + 1000)
    assert result["markets"][0]["observed_samples"] == 1
    assert result["markets"][0]["usable_quote_coverage_pct"] == 0.0
    assert result["markets"][0]["data_coverage_pct"] >= 80.0


# TEST H — volatility quiet period (zero moves valid)
def test_volatility_includes_quiet_zero_moves(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 60_000
    for i in range(10):
        store.write_book(enrich_book_row(_legacy_book_row(base + i * 5000, stale=False, mid=100.0)))
    store.close()
    result = analyze_range(settings, start_ms=base, end_ms=base + 60_000)
    market = result["markets"][0]
    assert market.get("p50_abs_mid_move_1s_bps") == 0.0


# TEST I — duplicate warnings at 85%
def test_warning_dedup_below_min_coverage() -> None:
    row = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 1.0,
        "data_coverage_pct": 85.0,
        "median_spread_bps": 3.0,
        "pct_time_spread_ge_5bps": 0.5,
        "median_two_sided_depth_10bps_usd": 500.0,
        "trades_per_minute_median": 2.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
    }
    scored = score_markets([row], thresholds=CandidateThresholds())
    warnings = scored[0].warnings
    assert any("strong penalty: observation coverage" in w for w in warnings)
    assert not any("data coverage below 95%" in w for w in warnings)


# TEST J — soft warning at 92%
def test_warning_soft_below_95() -> None:
    row = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 1.0,
        "data_coverage_pct": 92.0,
        "median_spread_bps": 3.0,
        "pct_time_spread_ge_5bps": 0.5,
        "median_two_sided_depth_10bps_usd": 500.0,
        "trades_per_minute_median": 2.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
    }
    scored = score_markets([row], thresholds=CandidateThresholds())
    warnings = scored[0].warnings
    assert any("data coverage below 95%" in w for w in warnings)
    assert not any("strong penalty: observation coverage" in w for w in warnings)


# TEST K — no coverage warning at 96%
def test_no_coverage_warning_at_96() -> None:
    row = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 1.0,
        "data_coverage_pct": 96.0,
        "median_spread_bps": 3.0,
        "pct_time_spread_ge_5bps": 0.5,
        "median_two_sided_depth_10bps_usd": 500.0,
        "trades_per_minute_median": 2.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
    }
    scored = score_markets([row], thresholds=CandidateThresholds())
    warnings = scored[0].warnings
    assert not any("coverage" in w.lower() for w in warnings)


# TEST — legacy spread recovery from bid/ask when spread_bps nulled
def test_legacy_spread_recovery(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    ts = int(time.time() * 1000)
    _write_legacy_parquet(tmp_path, [_legacy_book_row(ts, stale=True, mid=100.05, spread_bps=None)])
    result = analyze_range(settings, start_ms=ts - 1000, end_ms=ts + 1000)
    market = result["markets"][0]
    assert market.get("median_spread_bps") is not None
    assert market["median_spread_bps"] > 0
