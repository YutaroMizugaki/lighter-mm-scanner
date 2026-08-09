"""Round 2.1 correctness regressions."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_window
from lighter_mm.config import Settings
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.scoring import CandidateThresholds, score_markets
from lighter_mm.storage.backend import VersionedJson
from lighter_mm.storage.lock import LeaderLock
from lighter_mm.storage.parquet_store import ParquetStore


def _book_row(
    ts: int,
    *,
    stale: bool = False,
    market_id: int = 1,
    mid: float = 100.05,
    depth25: float = 400.0,
) -> dict:
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": "ETH",
        "best_bid": mid - 0.05,
        "best_ask": mid + 0.05,
        "mid": mid,
        "spread_absolute": 0.1,
        "spread_bps": None if stale else 10.0,
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
        "bid_depth_25bps_usd": 0.0 if stale else depth25,
        "ask_depth_25bps_usd": 0.0 if stale else depth25,
        "two_sided_depth_25bps_usd": 0.0 if stale else depth25,
    }


class _MockAtomicBackend:
    supports_atomic_cas = True

    def __init__(self) -> None:
        self.payload: dict | None = None
        self.generation: int | None = None
        self.upload_json_calls = 0
        self.cas_calls = 0

    def download_json_with_generation(self, _key: str) -> VersionedJson:
        return VersionedJson(self.payload, self.generation)

    def compare_and_swap_json(
        self, _key: str, payload: dict, *, if_generation_match: int
    ) -> bool:
        self.cas_calls += 1
        if if_generation_match == 0:
            if self.payload is not None:
                return False
        elif self.generation != if_generation_match:
            return False
        self.payload = payload
        self.generation = (self.generation or 0) + 1
        return True

    def upload_json(self, *_a, **_k) -> str:
        self.upload_json_calls += 1
        return "mock://"


def test_volatility_same_price_multi_origin(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=50, flush_seconds=60
    )
    base = int(time.time() * 1000) - 30_000
    # Repeating mid=100 at multiple origins; future mids differ at +5s
    series = [
        (0, 100.0),
        (5000, 101.0),
        (10000, 100.0),
        (15000, 103.0),
        (20000, 100.0),
        (25000, 105.0),
    ]
    for offset, mid in series:
        store.write_book(_book_row(base + offset, mid=mid, stale=False))
    store.close()
    result = analyze_window(settings, hours=1.0)
    market = result["markets"][0]
    p5 = market.get("p50_abs_mid_move_5s_bps")
    assert p5 is not None and p5 > 0


def test_gcs_cas_race_loser_cannot_overwrite() -> None:
    backend = _MockAtomicBackend()
    a = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    b = LeaderLock(backend, "lock.json", holder_id="b", lease_seconds=60)
    assert a.acquire("run1") is True
    gen_after_a = backend.generation
    assert b.acquire("run1") is False
    assert backend.upload_json_calls == 0
    assert backend.payload["holder_id"] == "a"
    assert backend.generation == gen_after_a


def test_renew_generation_conflict_returns_false() -> None:
    backend = _MockAtomicBackend()
    lock = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    assert lock.acquire("run1") is True
    backend.payload = {
        "holder_id": "other",
        "run_id": "runX",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    backend.generation = 999
    assert lock.renew("run1") is False
    assert backend.upload_json_calls == 0


def test_reference_mid_rejects_future_sample() -> None:
    hist = MidHistory()
    hist.add(1005, 101.0)
    pt = hist.nearest_at_or_before(1000)
    ref = pt.mid if pt and 0 <= 1000 - pt.ts_ms <= 3000 else None
    assert ref is None


def test_reference_mid_accepts_recent_past_sample() -> None:
    hist = MidHistory()
    hist.add(999, 100.5)
    pt = hist.nearest_at_or_before(1000)
    assert pt is not None
    assert 0 <= 1000 - pt.ts_ms <= 3000
    assert pt.mid == 100.5


def test_reference_mid_rejects_stale_past_sample() -> None:
    hist = MidHistory()
    hist.add(5000, 100.0)
    pt = hist.nearest_at_or_before(10000)
    ref = pt.mid if pt and 0 <= 10000 - pt.ts_ms <= 3000 else None
    assert ref is None


def test_coverage_counts_stale_first_period(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=5.0,
    )
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    base = int(datetime.now(UTC).timestamp() * 1000) - 600_000
    for i in range(120):
        ts = base + i * 5000
        store.write_book(_book_row(ts, stale=(i < 60)))
    store.close()
    result = analyze_window(settings, hours=10 / 60)
    market = result["markets"][0]
    cov = market["data_coverage_pct"]
    assert 40 <= cov <= 60


def test_new_market_tpm_uses_market_window(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    now = int(datetime.now(UTC).timestamp() * 1000)
    market_start = now - 2 * 3600 * 1000

    books = tmp_path / "book_samples" / "date=2026-08-09" / "hour=10"
    books.mkdir(parents=True)
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=100, flush_seconds=60
    )
    for i in range(24):
        store.write_book(_book_row(market_start + i * 300_000, stale=False, market_id=99))
    store.close()

    trades_dir = tmp_path / "trades" / "date=2026-08-09" / "hour=10"
    trades_dir.mkdir(parents=True)
    trade_ts = [market_start + int(i * (2 * 3600 * 1000) / 120) for i in range(120)]
    pq.write_table(
        pa.table(
            {
                "timestamp_ms": trade_ts,
                "market_id": [99] * 120,
                "symbol": ["NEW"] * 120,
                "trade_id": list(range(1, 121)),
                "usd_amount": [1.0] * 120,
                "type": ["trade"] * 120,
            }
        ),
        trades_dir / "part-0.parquet",
    )

    result = analyze_window(settings, hours=72.0)
    market = next(m for m in result["markets"] if m["market_id"] == 99)
    assert abs(market["trades_per_minute_mean"] - 1.0) < 0.15
    assert market["observation_hours"] < 3.0


def test_new_market_trades_per_hour_candidate(tmp_path: Path) -> None:
    row = {
        "market_id": 99,
        "symbol": "NEW",
        "observation_hours": 2.0,
        "analysis_window_hours": 72.0,
        "data_coverage_pct": 95.0,
        "trades_per_minute_mean": 1.0,
        "trades_per_minute_median": 0.0,
        "total_trade_count": 120,
        "median_two_sided_depth_10bps_usd": 800.0,
        "median_spread_bps": 3.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
        "markout_5s_count": 50,
        "markout_30s_count": 50,
        "pct_time_spread_ge_5bps": 0.5,
    }
    scored = score_markets([row], thresholds=CandidateThresholds())
    assert scored[0].candidate is True


def test_depth25_retained(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts, depth25=400.0))
    store.close()
    result = analyze_window(settings, hours=1.0)
    market = result["markets"][0]
    assert market.get("median_two_sided_depth_25bps_usd") == 400.0


def test_spread_persistence_respects_sample_interval(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        reports_dir=tmp_path / "reports",
        book_sample_interval_seconds=10.0,
    )
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10], flush_rows=10, flush_seconds=60
    )
    base = int(time.time() * 1000)
    for i in range(5):
        store.write_book(_book_row(base + i * 10_000, stale=False))
    store.close()
    result = analyze_window(settings, hours=1.0)
    assert result["markets"]
    assert result["markets"][0].get("pct_time_spread_ge_5bps") is not None
