"""Round 2.1 correctness regressions."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lighter_mm.analytics.aggregation import _trade_stats, analyze_window
from lighter_mm.config import Settings
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.engine.reference_mid import reference_mid_for_trade
from lighter_mm.scoring import CandidateThresholds, score_markets
from lighter_mm.storage.backend import VersionedJson
from lighter_mm.storage.lock import LeaderLock
from lighter_mm.storage.parquet_store import ParquetStore
from tests.helpers import enrich_book_row


def _book_row(
    ts: int,
    *,
    stale: bool = False,
    market_id: int = 1,
    mid: float = 100.05,
    depth25: float = 400.0,
) -> dict:
    return enrich_book_row({
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
    })


class _MockAtomicBackend:
    supports_atomic_cas = True

    def __init__(self) -> None:
        self.payload: dict | None = None
        self.generation: int | None = None
        self.upload_json_calls = 0
        self.cas_calls = 0
        self.mutate_before_next_cas: Callable[[], None] | None = None
        self._cas_lock = threading.Lock()

    def download_json_with_generation(self, _key: str) -> VersionedJson:
        return VersionedJson(self.payload, self.generation)

    def compare_and_swap_json(
        self, _key: str, payload: dict, *, if_generation_match: int
    ) -> bool:
        with self._cas_lock:
            self.cas_calls += 1
            if self.mutate_before_next_cas is not None:
                self.mutate_before_next_cas()
                self.mutate_before_next_cas = None
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


def _candidate_row(**overrides) -> dict:
    row = {
        "market_id": 1,
        "symbol": "TEST",
        "observation_hours": 2.0,
        "analysis_window_hours": 72.0,
        "data_coverage_pct": 95.0,
        "trades_per_minute_mean": 1.0,
        "trades_per_minute_median": 0.5,
        "total_trade_count": 120,
        "median_two_sided_depth_10bps_usd": 800.0,
        "median_spread_bps": 3.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
        "markout_5s_count": 50,
        "markout_30s_count": 50,
        "pct_time_spread_ge_5bps": 0.5,
        # Estimated Maker Fill candidate gate (>=100 samples).
        "estimated_maker_fill_samples": 100,
        "estimated_maker_fill_rate_30s_conservative": 0.3,
        "estimated_maker_fill_sample_quality": "preliminary",
    }
    row.update(overrides)
    return row


def _activity_candidate_row(observation_seconds: float, trade_count: int = 1) -> dict:
    obs_hours = observation_seconds / 3600.0
    tpm_mean = trade_count / max(observation_seconds / 60.0, 1.0 / 60.0)
    return _candidate_row(
        observation_hours=obs_hours,
        total_trade_count=trade_count,
        trades_per_minute_mean=tpm_mean,
        trades_per_minute_median=0.0,
    )


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

    # Each book row is an independent origin; 5 of 6 have a valid +5s future sample.
    assert market.get("volatility_5s_sample_count") == 5

    expected_moves = []
    for i in range(len(series) - 1):
        _t0, mid0 = series[i]
        t1, mid1 = series[i + 1]
        if t1 - _t0 == 5000:
            expected_moves.append(abs(math.log(mid1 / mid0)) * 10000.0)
    expected_p50 = sorted(expected_moves)[len(expected_moves) // 2]
    p5 = market.get("p50_abs_mid_move_5s_bps")
    assert p5 == pytest.approx(expected_p50, rel=1e-3)
    assert p5 != pytest.approx(0.0)


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


def test_acquire_stale_generation_cas_race() -> None:
    """Both contenders read expired lock at generation=N; only one CAS wins."""
    backend = _MockAtomicBackend()
    backend.payload = {
        "holder_id": "old",
        "run_id": "old",
        "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    backend.generation = 5

    read_barrier = threading.Barrier(2, timeout=5)
    original_download = backend.download_json_with_generation

    def synced_download(key: str) -> VersionedJson:
        result = original_download(key)
        read_barrier.wait(timeout=5)
        return result

    backend.download_json_with_generation = synced_download  # type: ignore[method-assign]

    a = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    b = LeaderLock(backend, "lock.json", holder_id="b", lease_seconds=60)

    results: dict[str, bool] = {}

    def try_acquire(lock: LeaderLock, name: str) -> None:
        results[name] = lock.acquire("run1")

    t_a = threading.Thread(target=try_acquire, args=(a, "a"))
    t_b = threading.Thread(target=try_acquire, args=(b, "b"))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    winners = [name for name, ok in results.items() if ok]
    losers = [name for name, ok in results.items() if not ok]
    assert len(winners) == 1
    assert len(losers) == 1
    assert backend.payload is not None
    assert backend.payload["holder_id"] == winners[0]
    assert backend.generation == 6
    assert backend.upload_json_calls == 0
    loser = a if losers[0] == "a" else b
    assert loser.cas_conflicts == 1


def test_renew_generation_conflict_returns_false() -> None:
    """Renew fails on generation conflict while holder_id stays the same."""
    backend = _MockAtomicBackend()
    lock = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    assert lock.acquire("run1") is True
    read_gen = backend.generation
    assert read_gen is not None

    def bump_generation() -> None:
        backend.generation = int(backend.generation or 0) + 1

    backend.mutate_before_next_cas = bump_generation
    assert lock.renew("run1") is False
    assert backend.upload_json_calls == 0
    assert backend.payload is not None
    assert backend.payload["holder_id"] == "a"
    assert lock.cas_conflicts == 1


def test_same_holder_concurrent_renew_does_not_lose_lock() -> None:
    """Collector renews from the event loop and from to_thread sync.

    GCS CAS is atomic: two overlapping same-holder renews must not look like
    lost leadership. In-process ops are serialized; generation conflicts from
    another writer still fail (see test_renew_generation_conflict_returns_false).
    """
    backend = _MockAtomicBackend()
    original_download = backend.download_json_with_generation

    def slow_download(key: str) -> VersionedJson:
        time.sleep(0.02)
        return original_download(key)

    backend.download_json_with_generation = slow_download  # type: ignore[method-assign]
    lock = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    assert lock.acquire("run1") is True

    n = 8
    results = [False] * n

    def worker(idx: int) -> None:
        results[idx] = lock.renew("run1")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert all(results)
    assert lock.cas_conflicts == 0
    assert lock.renew("run1") is True
    assert backend.payload is not None
    assert backend.payload["holder_id"] == "a"


def test_renew_after_release_does_not_revive_lease() -> None:
    backend = _MockAtomicBackend()
    lock = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    assert lock.acquire("run1") is True
    lock.release()
    assert backend.payload is not None
    assert backend.payload.get("released") is True
    released_expiry = backend.payload["expires_at"]
    released_gen = backend.generation

    assert lock.renew("run1") is False
    assert backend.payload.get("released") is True
    assert backend.payload["expires_at"] == released_expiry
    assert backend.generation == released_gen


def test_concurrent_release_and_renew_stays_released() -> None:
    backend = _MockAtomicBackend()
    lock = LeaderLock(backend, "lock.json", holder_id="a", lease_seconds=60)
    assert lock.acquire("run1") is True

    def do_release() -> None:
        lock.release()

    def do_renew() -> None:
        lock.renew("run1")

    t_release = threading.Thread(target=do_release)
    t_renew = threading.Thread(target=do_renew)
    t_release.start()
    t_renew.start()
    t_release.join(timeout=10)
    t_renew.join(timeout=10)
    assert not t_release.is_alive()
    assert not t_renew.is_alive()

    assert backend.payload is not None
    assert backend.payload.get("released") is True
    assert backend.payload["holder_id"] == "a"


def test_reference_mid_rejects_future_sample() -> None:
    hist = MidHistory()
    hist.add(1005, 101.0)
    assert reference_mid_for_trade(hist, 1000) is None


def test_reference_mid_accepts_recent_past_sample() -> None:
    hist = MidHistory()
    hist.add(999, 100.5)
    assert reference_mid_for_trade(hist, 1000) == 100.5


def test_reference_mid_rejects_stale_past_sample() -> None:
    hist = MidHistory()
    hist.add(5000, 100.0)
    assert reference_mid_for_trade(hist, 10000) is None


def test_reference_mid_accepts_exact_timestamp() -> None:
    hist = MidHistory()
    hist.add(1000, 100.25)
    assert reference_mid_for_trade(hist, 1000) == 100.25


def test_tpm_mean_uses_fractional_observation_seconds() -> None:
    df = pl.DataFrame(
        {
            "market_id": [1],
            "timestamp_ms": [1_000],
            "usd_amount": [1.0],
            "type": ["trade"],
        }
    )
    stats = _trade_stats(df, 1, observation_seconds=150.0)
    assert stats["trades_per_minute_mean"] == pytest.approx(0.4, rel=1e-6)
    assert stats["trades_per_minute_mean"] != pytest.approx(0.5)


def test_tpm_median_uses_wall_clock_minute_slots() -> None:
    """61s window spanning 3 UTC minute buckets — trade only in middle minute."""
    start = datetime(2026, 1, 1, 12, 0, 59, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = start_ms + 61_000
    trade_ms = start_ms + 1_000  # 12:01:00
    df = pl.DataFrame(
        {
            "market_id": [1],
            "timestamp_ms": [trade_ms],
            "usd_amount": [1.0],
            "type": ["trade"],
        }
    )
    stats = _trade_stats(
        df,
        1,
        observation_seconds=61.0,
        effective_start_ms=start_ms,
        effective_end_ms=end_ms,
    )
    # Buckets [12:00, 12:01, 12:02] => [0, 1, 0]; median = 0
    assert stats["trades_per_minute_median"] == 0.0
    assert stats["trades_per_minute_mean"] == pytest.approx(1.0 / (61.0 / 60.0), rel=1e-3)


def test_tpm_150s_boundary_activity_fail() -> None:
    row = _activity_candidate_row(150.0, trade_count=1)
    trades_per_hour = row["total_trade_count"] / row["observation_hours"]
    assert trades_per_hour == pytest.approx(24.0, rel=1e-3)
    scored = score_markets([row], thresholds=CandidateThresholds(min_trades_per_hour=30.0))
    assert scored[0].candidate is False


def test_tpm_120s_boundary_activity_pass() -> None:
    row = _activity_candidate_row(120.0, trade_count=1)
    trades_per_hour = row["total_trade_count"] / row["observation_hours"]
    assert trades_per_hour == pytest.approx(30.0, rel=1e-3)
    scored = score_markets(
        [row],
        thresholds=CandidateThresholds(
            min_trades_per_hour=30.0,
            min_observation_hours=0.01,
        ),
    )
    assert scored[0].candidate is True


def test_tpm_119s_boundary_activity_pass() -> None:
    row = _activity_candidate_row(119.0, trade_count=1)
    trades_per_hour = row["total_trade_count"] / row["observation_hours"]
    assert trades_per_hour == pytest.approx(30.25, rel=1e-2)
    scored = score_markets(
        [row],
        thresholds=CandidateThresholds(
            min_trades_per_hour=30.0,
            min_observation_hours=0.01,
        ),
    )
    assert scored[0].candidate is True


def test_short_observation_blocks_candidate_despite_strong_metrics() -> None:
    row = _candidate_row(
        observation_hours=10.0 / 60.0,
        total_trade_count=1,
        trades_per_minute_mean=6.0,
    )
    scored = score_markets([row], thresholds=CandidateThresholds(min_observation_hours=1.0))
    assert scored[0].candidate is False


def test_coverage_counts_inactive_with_mid_as_observed(tmp_path: Path) -> None:
    """Inactive (stale-flag) rows with a valid mid count toward observation coverage."""
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
    assert cov >= 95.0


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
    row = _candidate_row(
        market_id=99,
        symbol="NEW",
        observation_hours=2.0,
        trades_per_minute_mean=1.0,
        trades_per_minute_median=0.0,
        total_trade_count=120,
    )
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
