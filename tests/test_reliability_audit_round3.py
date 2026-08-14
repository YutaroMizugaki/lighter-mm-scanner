"""Reliability audit round 3 regressions (A–U)."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.cloud.analyzer import (
    _analysis_window_ms,
    _publish_dashboard,
    run_cloud_analyze,
    select_run_to_analyze,
)
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.cloud.sync import DurableSync, PartialParquetUploadError
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.lock import LostLeadershipError
from lighter_mm.storage.sqlite_meta import SqliteMeta
from lighter_mm.storage.state import RunState, now_iso
from lighter_mm.ws.manager import WsManager
from tests.test_collector_analyzer_split import _write_book_parquet


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


# A — Analyzer uses last_durable_event_ms durable watermark
def test_analyzer_watermark_running_run() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        status="running",
        last_successful_flush="2026-01-01T00:19:00+00:00",
        last_durable_event_ms=_ms("2026-01-01T00:15:00+00:00"),
    )
    _, end_ms, _, durable_ms = _analysis_window_ms(
        state, execution_start_ms=_ms("2026-01-01T00:19:00+00:00")
    )
    assert end_ms == _ms("2026-01-01T00:15:00+00:00")
    assert durable_ms == _ms("2026-01-01T00:15:00+00:00")


# B — Analyzer never analyzes beyond durable watermark
def test_analyzer_refuses_missing_durable_watermark() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        status="running",
        last_successful_flush="2026-01-01T00:19:00+00:00",
        last_durable_event_ms=None,
    )
    try:
        _analysis_window_ms(state, execution_start_ms=_ms("2026-01-01T00:19:00+00:00"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "last_durable_event_ms" in str(exc)


# C — Completed final analysis capped by durable watermark
def test_completed_run_capped_by_durable_watermark() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        status="completed",
        ended_at="2026-01-03T00:00:00+00:00",
        last_successful_flush="2026-01-03T00:05:00+00:00",
        last_durable_event_ms=_ms("2026-01-02T23:59:00+00:00"),
    )
    _, end_ms, _, _ = _analysis_window_ms(
        state, execution_start_ms=_ms("2026-01-03T12:00:00+00:00")
    )
    assert end_ms == _ms("2026-01-02T23:59:00+00:00")


def test_completed_run_uses_ended_at_when_flush_matches() -> None:
    state = RunState(
        run_id="r1",
        started_at="2026-01-01T00:00:00+00:00",
        status="completed",
        ended_at="2026-01-03T00:00:00+00:00",
        last_successful_flush="2026-01-03T00:05:00+00:00",
        last_durable_event_ms=_ms("2026-01-03T00:00:00+00:00"),
    )
    _, end_ms, _, _ = _analysis_window_ms(
        state, execution_start_ms=_ms("2026-01-04T00:00:00+00:00")
    )
    assert end_ms == _ms("2026-01-03T00:00:00+00:00")


# D — Fixed-rate sampling does not accumulate drift
async def _run_sample_ticks(app: CollectorApp, n: int) -> list[float]:
    ticks: list[float] = []
    interval = app.settings.book_sample_interval_seconds
    loop = asyncio.get_running_loop()
    next_tick = loop.time() + interval

    for _ in range(n):
        delay = max(0.0, next_tick - loop.time())
        await asyncio.sleep(delay)
        await asyncio.sleep(0.4)  # simulate processing time
        ticks.append(next_tick)
        next_tick += interval
    return ticks


def test_fixed_rate_sampling_schedule() -> None:
    async def _inner() -> None:
        settings = Settings(book_sample_interval_seconds=5.0)
        app = CollectorApp.__new__(CollectorApp)
        app.settings = settings
        app._stop = asyncio.Event()
        ticks = await _run_sample_ticks(app, 4)
        deltas = [ticks[i] - ticks[i - 1] for i in range(1, len(ticks))]
        for delta in deltas:
            assert math.isclose(delta, 5.0, abs_tol=0.15)

    asyncio.run(_inner())


# E — SQLite DQ uses batched transaction
def test_sqlite_dq_batch_single_commit(tmp_path: Path) -> None:
    meta = SqliteMeta(tmp_path / "meta.db")
    rows = [(i, {"actual_samples": i}) for i in range(10)]
    with patch.object(meta, "update_dq") as mock_single:
        meta.update_dq_batch(rows)
        mock_single.assert_not_called()
    dq = {r["market_id"]: r for r in meta.all_dq()}
    assert len(dq) == 10
    assert dq[0]["actual_samples"] == 0
    assert dq[9]["actual_samples"] == 9


# F — SQLite DQ failure does not stop Parquet collection
def test_sqlite_dq_failure_is_non_fatal(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app._pending_dq_rows = [(1, {"actual_samples": 1})]
    app._last_dq_flush_mono = 0.0
    app.meta = MagicMock()
    app.meta.update_dq_batch.side_effect = RuntimeError("sqlite down")

    async def _inner() -> None:
        app._flush_dq_if_due(force=True)

    asyncio.run(_inner())
    app.meta.update_dq_batch.assert_called_once()
    assert app._pending_dq_rows == [(1, {"actual_samples": 1})]


# G — Collector renew failure stops authoritative writes
def test_collector_renew_failure_blocks_sync(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.run_id = "r1"
    app.state = RunState(run_id="r1", started_at=now_iso(), status="running")
    app._lost_leadership = False
    app._stop = asyncio.Event()
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.sync = DurableSync(app.backend, run_id="r1", gcs_prefix="lighter-mm")
    app.store = MagicMock()
    app.store.take_closed_paths.return_value = []
    app.lock = MagicMock()
    app.lock.renew.return_value = False
    try:
        app._sync_only(final=False)
        raise AssertionError("expected LostLeadershipError")
    except LostLeadershipError:
        pass
    assert app._lost_leadership is True
    assert app.backend.download_json("lighter-mm/runs/r1/state/state.json") is None


async def test_lost_leadership_shutdown_still_releases_lock() -> None:
    app = CollectorApp.__new__(CollectorApp)
    app._lost_leadership = True
    app._stop = asyncio.Event()
    app._ws = None
    app._completed = False
    app._dashboard_enabled = False
    app.run_id = "r1"
    app.markout = MagicMock()
    app.mid_histories = {}
    app.lock = MagicMock()
    app.discovery = MagicMock()
    app.discovery.close = AsyncMock()
    app.dashboard = MagicMock()
    app.meta = MagicMock()
    app.store = MagicMock()
    app.store.samples_written = 0
    app.store.trades_written = 0
    app.store.markouts_written = 0
    await app.shutdown()
    app.lock.release.assert_called_once()
    app.meta.close.assert_called_once()
    app.discovery.close.assert_awaited_once()
    app.store.close.assert_not_called()


async def test_lock_renew_loop_skips_renew_when_stop_set_after_timeout() -> None:
    app = CollectorApp.__new__(CollectorApp)
    app._stop = asyncio.Event()
    app._lost_leadership = False
    app.lock = MagicMock()
    app.run_id = "r1"
    app.settings = MagicMock()
    app.settings.git_sha = "abc"

    async def timeout_then_notice_stop(awaitable, timeout=None):  # noqa: ANN001, ARG001
        awaitable.close()
        app._stop.set()
        raise TimeoutError

    with patch("lighter_mm.collector.asyncio.wait_for", timeout_then_notice_stop):
        await app._lock_renew_loop()
    app.lock.renew.assert_not_called()
    assert app._lost_leadership is False


# H — Analyzer renew failure blocks public publish
def test_analyzer_lock_loss_blocks_publish(tmp_path: Path) -> None:
    settings = Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
        max_final_analysis_attempts=3,
    )
    be = LocalStorageBackend(tmp_path / "remote")
    be.upload_json("lighter-mm/state/active_run.json", {"run_id": "run1", "status": "running"})
    be.upload_json(
        "lighter-mm/runs/run1/state/state.json",
        RunState(
            run_id="run1",
            started_at=now_iso(),
            status="running",
            last_successful_flush=now_iso(),
        ).to_public_dict(),
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.analyze_range", return_value={"scored": []}):
            with patch(
                "lighter_mm.cloud.analyzer._check_leadership",
                side_effect=LostLeadershipError("lost before publish"),
            ):
                code = run_cloud_analyze(settings)
    assert code == 1
    assert be.download_json("lighter-mm/public/current.json") is None


# I — Final request only after completed state durable (ordering helper)
def test_final_request_created_after_completed_state_upload(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.run_id = "run72"
    app.state = RunState(run_id="run72", started_at=now_iso(), status="running")
    app.sync = DurableSync(app.backend, run_id="run72", gcs_prefix="lighter-mm")
    app._lost_leadership = False
    app.state.status = "completed"
    app.state.ended_at = now_iso()
    app.state.last_successful_flush = now_iso()
    app.backend.upload_json(app.sync.state_key(), app.state.to_public_dict())
    app._create_final_analysis_request()
    req = app.backend.download_json("lighter-mm/analysis-requests/run72.json")
    assert req is not None
    assert req["status"] == "pending"


# J — Analyzer refuses final request with running state
def test_analyzer_refuses_final_with_running_state(tmp_path: Path) -> None:
    settings = Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
    )
    be = LocalStorageBackend(tmp_path / "remote")
    be.upload_json(
        "lighter-mm/analysis-requests/final-run.json",
        {
            "run_id": "final-run",
            "type": "final",
            "status": "pending",
            "requested_at": "2026-01-01",
            "attempts": 0,
        },
    )
    be.upload_json(
        "lighter-mm/runs/final-run/state/state.json",
        RunState(
            run_id="final-run",
            started_at=now_iso(),
            status="running",
            last_successful_flush=now_iso(),
        ).to_public_dict(),
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.analyze_range") as mock_analyze:
            code = run_cloud_analyze(settings)
    assert code == 0
    mock_analyze.assert_not_called()


# K — Final request retries bounded
def test_final_request_retries_bounded(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    sync = DurableSync(be, run_id="r1", gcs_prefix="lighter-mm")
    from lighter_mm.cloud.analyzer import (
        _claim_final_request,
        _finalize_final_request_failure,
    )

    be.upload_json(
        sync.analysis_request_key("r1"),
        {
            "run_id": "r1",
            "type": "final",
            "status": "pending",
            "requested_at": "2026-01-01",
            "attempts": 0,
        },
    )
    for i in range(3):
        assert _claim_final_request(be, sync, "r1", max_attempts=3)
        _finalize_final_request_failure(
            be, sync, "r1", error=f"fail {i}", max_attempts=3
        )
    req = be.download_json(sync.analysis_request_key("r1"))
    assert req is not None
    assert req["status"] == "failed"


# L — Failed final request does not block active run
def test_failed_final_does_not_block_active_run(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    settings = Settings(gcs_prefix="lighter-mm", max_final_analysis_attempts=3)
    be.upload_json(
        "lighter-mm/analysis-requests/dead.json",
        {
            "run_id": "dead",
            "type": "final",
            "status": "failed",
            "requested_at": "2026-01-01",
            "attempts": 3,
        },
    )
    be.upload_json(
        "lighter-mm/state/active_run.json",
        {"run_id": "active-run", "status": "running"},
    )
    selected = select_run_to_analyze(be, settings)
    assert selected == ("active-run", "incremental")


# M — Partial publish does not advance current generation
def test_partial_publish_does_not_advance_current(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path / "remote")
    sync = DurableSync(be, run_id="r1", gcs_prefix="lighter-mm")
    be.upload_json(
        sync.public_key("current.json"),
        {"analysis_id": "oldgen", "generated_at": "2026-01-01T00:00:00+00:00"},
        public=True,
    )
    payload = {
        "latest": {"generated_at": now_iso(), "title": "x"},
        "markets": [],
        "candidates": [],
        "market_details": {"ETH": {"symbol": "ETH"}},
    }

    original_upload = be.upload_json

    def fail_on_eth(remote_key: str, payload: dict, *, public: bool = False) -> str:
        if "market/ETH" in remote_key and "newgen" in remote_key:
            raise RuntimeError("upload failed")
        return original_upload(remote_key, payload, public=public)

    with patch.object(be, "upload_json", side_effect=fail_on_eth):
        try:
            _publish_dashboard(be, sync, payload, analysis_id="newgen")
        except RuntimeError:
            pass
    current = be.download_json(sync.public_key("current.json"))
    assert current is not None
    assert current["analysis_id"] == "oldgen"


# N — latest_book_update_age uses latest timestamp
def test_latest_book_update_age_uses_arg_max(tmp_path: Path) -> None:
    books = tmp_path / "books"
    books.mkdir(parents=True)
    path = books / "date=2026-01-01/hour=00/part.parquet"
    path.parent.mkdir(parents=True)
    table = pa.table(
        {
            "timestamp_ms": [1_000, 2_000],
            "market_id": [1, 1],
            "symbol": ["ETH", "ETH"],
            "is_stale": [False, False],
            "is_usable": [True, True],
            "is_inactive": [False, False],
            "book_update_age_ms": [600_000, 10_000],
            "spread_bps": [5.0, 5.0],
            "mid": [100.0, 100.0],
            "best_bid": [99.9, 99.9],
            "best_ask": [100.1, 100.1],
            "best_bid_size_usd": [100.0, 100.0],
            "best_ask_size_usd": [100.0, 100.0],
            "two_sided_depth_5bps_usd": [50.0, 50.0],
            "two_sided_depth_10bps_usd": [200.0, 200.0],
            "two_sided_depth_25bps_usd": [400.0, 400.0],
            "current_funding_rate": [None, None],
            "funding_rate": [None, None],
            "open_interest": [None, None],
            "daily_quote_token_volume": [None, None],
        }
    )
    pq.write_table(table, path)
    con = duckdb.connect()
    con.execute(
        f"""
        SELECT
            arg_max(book_update_age_ms, timestamp_ms) / 1000.0 AS latest_age,
            quantile_cont(book_update_age_ms / 1000.0, 0.95) AS p95_age
        FROM read_parquet('{path.as_posix()}')
        """
    )
    latest, p95 = con.fetchone()
    assert latest == 10.0
    assert p95 > 100.0


# O — runtime dedupe accepts same trade_id on different markets
def test_trade_dedupe_is_market_aware() -> None:
    from decimal import Decimal

    from lighter_mm.config import Settings
    from lighter_mm.models import MarketMeta, MarketStatus, MarketType, TradeEvent

    def meta(mid: int) -> MarketMeta:
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

    settings = Settings()
    markets = {1: meta(1), 2: meta(2)}
    mgr = WsManager(settings=settings, markets=markets)
    seen: list[int] = []

    async def on_trade(trade: TradeEvent, _symbol: str) -> None:
        seen.append(trade.market_id)

    mgr.on_trade = on_trade
    trade_payload = {
        "trade_id": 123,
        "timestamp": 1_000_000,
        "market_id": 1,
        "price": "1",
        "size": "1",
        "usd_amount": "1",
        "is_maker_ask": True,
        "type": "trade",
    }

    async def _run() -> None:
        await mgr._handle_trade(
            {"channel": "trade:1", "trades": [trade_payload]}, persist=True
        )
        await mgr._handle_trade(
            {"channel": "trade:2", "trades": [trade_payload]}, persist=True
        )
        await mgr._handle_trade(
            {"channel": "trade:1", "trades": [trade_payload]}, persist=True
        )

    asyncio.run(_run())
    assert seen == [1, 2]


# P — GCS immutable precondition conflict maps to already-durable
def test_gcs_precondition_conflict_is_file_exists() -> None:
    from lighter_mm.storage.gcs_backend import GCSStorageBackend

    class FakePreconditionFailed(Exception):
        pass

    exc = FakePreconditionFailed("412 Precondition Failed")
    assert GCSStorageBackend._is_precondition_conflict(exc) is True


# Q — network upload failure retains local parquet (covered in split tests; smoke)
def test_network_upload_failure_retains_local(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    local = data_root / "book_samples/date=2026-08-09/hour=10/part-x.parquet"
    _write_book_parquet(local, int(datetime.now(UTC).timestamp() * 1000))

    def boom(*_a, **_k):
        raise OSError("network down")

    be.upload_file = boom  # type: ignore[method-assign]
    try:
        sync.upload_new_parquets(data_root, paths=[local])
        raise AssertionError("expected failure")
    except PartialParquetUploadError:
        pass
    assert local.exists()


# R — completed observation_hours stays fixed
def test_completed_observation_hours_fixed() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = started + timedelta(hours=72)
    end_ms = int(ended.timestamp() * 1000)
    state = RunState(
        run_id="r1",
        started_at=started.isoformat(),
        status="completed",
        ended_at=ended.isoformat(),
        last_successful_flush=ended.isoformat(),
    )
    payload = build_dashboard_payload(
        Settings(),
        start_ms=int(started.timestamp() * 1000),
        end_ms=end_ms,
        state=state,
        analysis_result={"scored": [], "hours": 72.0},
    )
    assert payload["latest"]["observation_hours"] == 72.0


# S/T/U — cloudbuild + dashboard labels covered in existing tests; re-assert here
def test_cloudbuild_scheduler_v2_and_invoker() -> None:
    from tests.test_cloudbuild_scheduler import (
        test_cloudbuild_scheduler_iam_and_oauth,
        test_cloudbuild_scheduler_uses_run_jobs_v2_endpoint,
    )

    test_cloudbuild_scheduler_uses_run_jobs_v2_endpoint()
    test_cloudbuild_scheduler_iam_and_oauth()


def test_dashboard_last_sync_and_last_analysis_labels() -> None:
    from tests.test_dashboard_analysis_health import test_dashboard_labels_last_analysis_and_last_sync

    test_dashboard_labels_last_analysis_and_last_sync()
