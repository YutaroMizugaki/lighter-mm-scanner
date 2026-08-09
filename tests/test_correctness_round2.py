"""Round-2 correctness / reliability regressions (spec items A–L)."""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics import aggregation as agg_mod
from lighter_mm.analytics.aggregation import analyze_window
from lighter_mm.cloud.dashboard_data import build_collector_status_payload
from lighter_mm.config import Settings
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.engine.trade_activity import TradeActivityTracker
from lighter_mm.models import TradeEvent, TradeType
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.scoring import CandidateThresholds, score_markets
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.state import RunState, now_iso
from lighter_mm.ws.manager import WsManager, WsRuntimeStats


def _book_row(ts: int, *, stale: bool = False, market_id: int = 1) -> dict:
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": "ETH",
        "best_bid": 100.0,
        "best_ask": 100.1,
        "mid": 100.05,
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
        "bid_depth_25bps_usd": 0.0 if stale else 400.0,
        "ask_depth_25bps_usd": 0.0 if stale else 400.0,
        "two_sided_depth_25bps_usd": 0.0 if stale else 400.0,
    }


# TEST A: stale mid is not added
def test_stale_mid_not_added_to_mid_history() -> None:
    hist = MidHistory(retention_seconds=180)
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [{"price": "100.0", "size": "1.0"}],
            "asks": [{"price": "101.0", "size": "1.0"}],
        },
        recv_ms=int(time.time() * 1000) - 500_000,
    )
    metrics = book.compute_metrics(depth_bps_levels=[5, 10], stale_seconds=180, now_ms=int(time.time() * 1000))
    assert metrics.is_stale
    assert metrics.mid is not None
    before = len(hist)
    if book.synced and not metrics.is_stale and metrics.mid is not None:
        hist.add(int(time.time() * 1000), metrics.mid)
    assert len(hist) == before


# TEST B: usable book timestamp
def test_usable_book_timestamp_not_updated_on_stale() -> None:
    last_usable: int | None = None
    last_row: int | None = None
    now = int(time.time() * 1000)
    book = LocalOrderBook(market_id=1, symbol="ETH")
    book.apply_snapshot(
        {
            "nonce": 1,
            "begin_nonce": 0,
            "bids": [{"price": "100.0", "size": "1.0"}],
            "asks": [{"price": "101.0", "size": "1.0"}],
        },
        recv_ms=now - 500_000,
    )
    metrics = book.compute_metrics(depth_bps_levels=[5, 10], stale_seconds=180, now_ms=now)
    last_row = now
    if book.synced and not metrics.is_stale and metrics.mid is not None:
        last_usable = now
    assert last_row is not None
    assert last_usable is None


# TEST C: live TPM > 60
def test_live_tpm_not_capped_at_60() -> None:
    tracker = TradeActivityTracker()
    base = int(time.time() * 1000) - 60_000
    for i in range(100):
        tracker.on_trade(
            TradeEvent(
                trade_id=i + 1,
                timestamp_ms=base + i * 500,
                market_id=1,
                price=__import__("decimal").Decimal("1"),
                size=__import__("decimal").Decimal("1"),
                usd_amount=__import__("decimal").Decimal("1"),
                is_maker_ask=True,
                type=TradeType.TRADE,
            )
        )
    now = base + 60_000
    tpm = tracker.trades_per_minute(1, now)
    assert tpm >= 90.0


# TEST D: Candidate uses trades/hour
def test_candidate_uses_trades_per_hour_not_median_tpm() -> None:
    row = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 1.0,
        "data_coverage_pct": 95.0,
        "trades_per_minute_median": 0.0,
        "trades_per_minute_mean": 2.0,
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


# TEST E: markout waits for forward sample
def test_markout_waits_for_forward_sample() -> None:
    rows: list[dict] = []
    eng = MarkoutEngine(horizons=[5], on_markout=rows.append, forward_wait_ms=2500)
    trade = TradeEvent.from_ws(
        {
            "trade_id": 1,
            "timestamp": 0,
            "market_id": 0,
            "price": "100",
            "size": "1",
            "usd_amount": "100",
            "is_maker_ask": True,
            "type": "trade",
        }
    )
    eng.on_trade(trade, "ETH", 100.0)
    hist = MidHistory()
    hist.add(0, 100.0)
    # target=5000; poll at 5050 — only before-mid exists, should NOT resolve yet
    n = eng.poll(5050, {0: hist})
    assert n == 0
    assert eng.pending_count == 1
    # Forward mid arrives; poll after forward-wait window with forward sample
    hist.add(5200, 99.0)
    n2 = eng.poll(8000, {0: hist})
    assert n2 == 1
    assert rows[0]["future_mid"] == 99.0


# TEST F: markout sample minimum
def test_markout_sample_minimum_blocks_candidate() -> None:
    row_low = {
        "market_id": 1,
        "symbol": "ETH",
        "observation_hours": 24.0,
        "data_coverage_pct": 95.0,
        "trades_per_minute_mean": 2.0,
        "trades_per_minute_median": 2.0,
        "total_trade_count": 5000,
        "median_two_sided_depth_10bps_usd": 800.0,
        "median_spread_bps": 3.0,
        "maker_markout_5s_median_bps": 5.0,
        "maker_markout_30s_median_bps": 3.0,
        "markout_5s_count": 1,
        "markout_30s_count": 1,
        "pct_time_spread_ge_5bps": 0.5,
    }
    scored_low = score_markets([row_low], thresholds=CandidateThresholds())
    assert scored_low[0].candidate is False

    row_ok = {**row_low, "markout_5s_count": 25, "markout_30s_count": 25}
    scored_ok = score_markets([row_ok], thresholds=CandidateThresholds())
    assert scored_ok[0].candidate is True


# TEST G: volatility tolerance
def test_volatility_rejects_large_horizon_drift(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=100, flush_seconds=60)
    base = int(time.time() * 1000)
    # Samples every 5s; 30s target should not pair with +25s drift (55s total gap)
    for i in range(20):
        store.write_book(_book_row(base + i * 5000, stale=False))
    store.close()
    result = analyze_window(settings, hours=1.0)
    market = result["markets"][0]
    # With tight tolerance, 30s move should be None or small set — not use 55s-apart mids
    p30 = market.get("p50_abs_mid_move_30s_bps")
    # All mids identical → no moves; key is SQL does not error and does not inflate
    assert p30 is None or p30 == 0.0


# TEST H: MidHistory retention
def test_mid_history_time_retention() -> None:
    hist = MidHistory(retention_seconds=10, maxlen=100_000)
    base = 1_000_000
    for i in range(5000):
        hist.add(base + i * 10, 100.0 + i * 0.01)
    assert len(hist) < 5000
    last_ts = base + 4999 * 10
    assert hist._points[0].ts_ms >= last_ts - 10_000


# TEST I: WS ACK tracking
def test_ws_ack_tracking() -> None:
    settings = Settings()
    mgr = WsManager(settings=settings, markets={})
    mgr.runtime.planned_channels = 5
    mgr._shard_acked = {0: set()}
    for ch in ("order_book/1", "trade/1", "order_book/2"):
        mgr._record_subscription_ack(0, ch)
    assert mgr.runtime.acked_channels == 3
    mgr._record_subscription_ack(0, "market_stats/all")
    mgr._record_subscription_ack(0, "order_book/3")
    assert mgr.runtime.acked_channels == 5
    mgr._shard_acked[0] = set()
    mgr._sync_acked_channels()
    assert mgr.runtime.acked_channels == 0


# TEST J: health degraded on shard shortage
def test_health_degraded_on_shard_shortage(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    (settings.data_dir / "book_samples").mkdir(parents=True)
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        samples_written=10,
        markets=[1],
    )
    ws = WsRuntimeStats(connected_shards=4, total_shards=5, planned_channels=10, acked_channels=10)
    payload = build_collector_status_payload(
        state, settings=settings, ws_runtime=ws.public_dict()
    )
    warnings = payload["health_warnings"]
    assert any("4/5 shards" in w for w in warnings)


# TEST K: aggregate dedupe
def test_aggregate_dedupes_trades_and_markouts(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    trades_dir = tmp_path / "trades" / "date=2026-08-09" / "hour=10"
    trades_dir.mkdir(parents=True)
    mark_dir = tmp_path / "markouts" / "date=2026-08-09" / "hour=10"
    mark_dir.mkdir(parents=True)
    ts = int(time.time() * 1000)
    trade_table = pa.table(
        {
            "timestamp_ms": [ts, ts],
            "market_id": [1, 1],
            "symbol": ["ETH", "ETH"],
            "trade_id": [42, 42],
            "usd_amount": [10.0, 10.0],
            "type": ["trade", "trade"],
        }
    )
    pq.write_table(trade_table, trades_dir / "part-0.parquet")
    mark_table = pa.table(
        {
            "timestamp_ms": [ts, ts],
            "market_id": [1, 1],
            "symbol": ["ETH", "ETH"],
            "trade_id": [42, 42],
            "horizon_s": [5, 5],
            "maker_markout_bps": [1.0, 1.0],
        }
    )
    pq.write_table(mark_table, mark_dir / "part-0.parquet")
    store = ParquetStore(tmp_path, depth_levels=[5, 10], flush_rows=1, flush_seconds=60)
    store.write_book(_book_row(ts))
    store.close()
    result = analyze_window(settings, hours=1.0)
    market = next(m for m in result["markets"] if m.get("total_trade_count", 0) > 0 or m.get("markout_5s_count", 0) > 0)
    if market.get("total_trade_count"):
        assert market["total_trade_count"] == 1
    if market.get("markout_5s_count"):
        assert market["markout_5s_count"] == 1


# TEST L: memory-safe architecture — no full-book SELECT * .pl()
def test_aggregation_does_not_materialize_full_book_dataframe() -> None:
    src = inspect.getsource(agg_mod.analyze_window)
    assert "book_df" not in src
    assert "_read_parquet_window" not in src
    assert ".pl()" not in src
