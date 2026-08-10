"""Market lifecycle: mid-window add/inactive and coverage semantics."""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.config import Settings
from lighter_mm.models import MarketMeta, MarketStatus, MarketType
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.runtime.market_lifecycle import apply_market_refresh, record_market_added
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.state import MarketLifecycleEntry, RunState, now_iso
from tests.helpers import enrich_book_row
from tests.test_coverage_semantics import _legacy_book_row, _new_book_row


def _lifecycle(
    market_id: int,
    first_active_at_ms: int,
    removed_at_ms: int | None = None,
) -> dict[int, MarketLifecycleEntry]:
    return {
        market_id: MarketLifecycleEntry(
            first_active_at_ms=first_active_at_ms,
            removed_at_ms=removed_at_ms,
        )
    }


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
        market_lifecycle=_lifecycle(1, base),
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
    result = analyze_range(
        settings,
        start_ms=base,
        end_ms=end_ms,
        market_lifecycle=_lifecycle(99, late_start),
    )
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
        market_lifecycle=_lifecycle(7, base, inactive_at),
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
        market_lifecycle=_lifecycle(market_id, base),
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["last_observed_ms"] == outage_at
    assert market["observation_coverage_pct"] < 95.0
    assert 45.0 <= market["observation_coverage_pct"] <= 55.0


def test_formal_inactive_after_collector_outage_not_hidden(tmp_path: Path) -> None:
    """Collector stops at 11:00; market removed at 11:30; analysis to 12:00 → ~66.7%."""
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 7_200_000
    outage_at = base + 3_600_000
    removed_at = base + 5_400_000
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
        market_lifecycle=_lifecycle(market_id, base, removed_at),
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["observation_coverage_pct"] < 95.0
    assert 63.0 <= market["observation_coverage_pct"] <= 70.0


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
        market_lifecycle=_lifecycle(market_id, base),
    )
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["observation_coverage_pct"] < 95.0
    assert market["observation_coverage_pct"] > 70.0


def test_local_fallback_without_lifecycle_hides_no_outage(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=50, flush_seconds=60)
    base = int(time.time() * 1000) - 7_200_000
    outage_at = base + 3_600_000
    end_ms = base + 7_200_000
    market_id = 5
    for i in range(1440):
        ts = base + i * 5000
        if ts > outage_at:
            break
        store.write_book(
            enrich_book_row(_legacy_book_row(ts, stale=False, market_id=market_id, mid=100.0))
        )
    store.close()
    result = analyze_range(settings, start_ms=base, end_ms=end_ms)
    market = next(m for m in result["markets"] if m["market_id"] == market_id)
    assert market["observation_coverage_pct"] < 95.0


def _meta(mid: int) -> MarketMeta:
    return MarketMeta(
        market_id=mid,
        symbol=f"M{mid}",
        market_type=MarketType.PERP,
        status=MarketStatus.ACTIVE,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        min_base_amount=Decimal("0.01"),
        min_quote_amount=Decimal("1"),
        supported_price_decimals=2,
        supported_size_decimals=4,
    )


def test_market_lifecycle_persists_across_state_roundtrip() -> None:
    state = RunState(run_id="r1", started_at=now_iso())
    at_ms = 1_700_000_000_000
    record_market_added(state, 10, at_ms)
    record_market_added(state, 20, at_ms)
    apply_market_refresh(state, added=[], removed=[_meta(20)], at_ms=at_ms + 60_000)
    raw = state.model_dump()
    restored = RunState.model_validate(raw)
    assert restored.market_lifecycle[10].first_active_at_ms == at_ms
    assert restored.market_lifecycle[10].removed_at_ms is None
    assert restored.market_lifecycle[20].removed_at_ms == at_ms + 60_000
