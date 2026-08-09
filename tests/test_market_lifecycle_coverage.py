"""Market lifecycle: mid-window add/inactive and coverage semantics."""

from __future__ import annotations

import time
from pathlib import Path

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.config import Settings
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.storage.parquet_store import ParquetStore
from tests.helpers import enrich_book_row
from tests.test_coverage_semantics import _legacy_book_row, _new_book_row


def test_unusable_rows_reduce_usable_not_observation_coverage(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 600_000
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
    for i in range(120):
        ts = base + i * 5000
        row = _new_book_row(ts, book, settings=settings)
        if i >= 60:
            row["is_usable"] = False
        store.write_book(row)
    store.close()
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=base + 600_000,
        active_market_ids={1},
    )
    market = result["markets"][0]
    assert market["observed_samples"] == 120
    assert market["observation_coverage_pct"] >= 95.0
    assert market["usable_quote_coverage_pct"] == 50.0
    assert market["data_coverage_pct"] == market["observation_coverage_pct"]


def test_market_added_mid_window_uses_active_window(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 3_600_000
    late_start = base + 1_800_000
    end_ms = base + 3_600_000
    for i in range(720):
        ts = base + i * 5000
        if ts < late_start or ts > end_ms:
            continue
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=99, mid=100.0))
        )
    store.close()
    result = analyze_range(settings, start_ms=base, end_ms=end_ms)
    market = next(m for m in result["markets"] if m["market_id"] == 99)
    assert market["first_observed_ms"] == late_start
    assert market["observation_coverage_pct"] >= 95.0


def test_market_inactive_mid_window_caps_expected_samples(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 3_600_000
    inactive_at = base + 1_800_000
    end_ms = base + 3_600_000
    for i in range(720):
        ts = base + i * 5000
        if ts > inactive_at:
            break
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=7, mid=100.0))
        )
    store.close()
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=end_ms,
        active_market_ids=set(),
    )
    market = next(m for m in result["markets"] if m["market_id"] == 7)
    assert market["last_observed_ms"] == inactive_at
    assert market["observation_coverage_pct"] >= 95.0


def test_active_market_trailing_collector_outage_lowers_coverage(tmp_path: Path) -> None:
    """Active market with collector stop after 1h in a 2h window → ~50% coverage."""
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 7_200_000
    outage_at = base + 3_600_000
    end_ms = base + 7_200_000
    market_id = 42
    for i in range(1440):
        ts = base + i * 5000
        if ts > outage_at:
            break
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=market_id, mid=100.0))
        )
    store.close()
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=end_ms,
        active_market_ids={market_id},
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["last_observed_ms"] == outage_at
    assert market["observation_coverage_pct"] < 95.0
    assert 45.0 <= market["observation_coverage_pct"] <= 55.0


def test_formal_inactive_at_outage_time_preserves_coverage(tmp_path: Path) -> None:
    """Same trailing data gap but market inactive at outage time → ~100% coverage."""
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 7_200_000
    inactive_at = base + 3_600_000
    end_ms = base + 7_200_000
    market_id = 42
    for i in range(1440):
        ts = base + i * 5000
        if ts > inactive_at:
            break
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=market_id, mid=100.0))
        )
    store.close()
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=end_ms,
        active_market_ids=set(),
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["observation_coverage_pct"] >= 95.0


def test_mid_window_collector_gap_reduces_coverage(tmp_path: Path) -> None:
    """10-minute collector outage mid-window lowers coverage proportionally."""
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 3_600_000
    end_ms = base + 3_600_000
    gap_start = base + 1_500_000
    gap_end = gap_start + 600_000
    market_id = 11
    for i in range(720):
        ts = base + i * 5000
        if gap_start <= ts < gap_end:
            continue
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=market_id, mid=100.0))
        )
    store.close()
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=end_ms,
        active_market_ids={market_id},
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["observation_coverage_pct"] < 95.0
    assert market["observation_coverage_pct"] > 70.0
