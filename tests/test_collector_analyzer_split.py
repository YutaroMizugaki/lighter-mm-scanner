"""Regression tests for collector/analyzer architecture split."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from tests.conftest import enrich_book_row

from lighter_mm.analytics.aggregation import AnalysisSources, analyze_range
from lighter_mm.cloud.analyzer import select_run_to_analyze
from lighter_mm.cloud.dashboard_data import build_collector_status_payload
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.lock import LeaderLock
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.state import RunState, now_iso


def _write_book_parquet(path: Path, ts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "timestamp_ms": [ts],
            "market_id": [1],
            "symbol": ["ETH"],
            "best_bid": [100.0],
            "best_ask": [100.1],
            "mid": [100.05],
            "spread_absolute": [0.1],
            "spread_bps": [10.0],
            "best_bid_size_base": [1.0],
            "best_ask_size_base": [1.0],
            "best_bid_size_usd": [100.0],
            "best_ask_size_usd": [100.1],
            "is_stale": [False],
            "nonce": [1],
            "index_price": [None],
            "mark_price": [None],
            "stats_mid_price": [None],
            "open_interest": [None],
            "last_trade_price": [None],
            "current_funding_rate": [None],
            "funding_rate": [None],
            "daily_base_token_volume": [None],
            "daily_quote_token_volume": [None],
            "daily_price_low": [None],
            "daily_price_high": [None],
            "daily_price_change": [None],
            "bid_depth_5bps_usd": [50.0],
            "ask_depth_5bps_usd": [50.0],
            "two_sided_depth_5bps_usd": [50.0],
            "bid_depth_10bps_usd": [200.0],
            "ask_depth_10bps_usd": [200.0],
            "two_sided_depth_10bps_usd": [200.0],
            "bid_depth_25bps_usd": [400.0],
            "ask_depth_25bps_usd": [400.0],
            "two_sided_depth_25bps_usd": [400.0],
        }
    )
    pq.write_table(table, path)


# TEST A — upload success deletes local closed parquet
def test_upload_success_deletes_local_closed_parquet(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    local = data_root / "book_samples/date=2026-08-09/hour=10/part-x.parquet"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"PAR1")
    uploaded = sync.upload_new_parquets(data_root, paths=[local])
    assert len(uploaded) == 1
    assert not local.exists()


# TEST B — upload failure retains local file
def test_upload_failure_retains_local_file(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")
    local = data_root / "book_samples/date=2026-08-09/hour=10/part-x.parquet"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"PAR1")

    def boom(*_a, **_k):
        raise OSError("network down")

    be.upload_file = boom  # type: ignore[method-assign]
    try:
        sync.upload_new_parquets(data_root, paths=[local])
        raise AssertionError("expected upload failure")
    except OSError:
        pass
    assert local.exists()


# TEST C — unique parquet filenames on repeated rotation
def test_parquet_filenames_unique_on_rotation(tmp_path: Path) -> None:
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60, rotation_minutes=15
    )
    ts = int(time.time() * 1000)
    row = enrich_book_row({
        "timestamp_ms": ts,
        "market_id": 1,
        "symbol": "ETH",
        "best_bid": 1.0,
        "best_ask": 1.1,
        "mid": 1.05,
        "spread_absolute": 0.1,
        "spread_bps": 10.0,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 1.0,
        "best_ask_size_usd": 1.1,
        "is_stale": False,
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
        "bid_depth_5bps_usd": 1.0,
        "ask_depth_5bps_usd": 1.0,
        "two_sided_depth_5bps_usd": 1.0,
        "bid_depth_10bps_usd": 1.0,
        "ask_depth_10bps_usd": 1.0,
        "two_sided_depth_10bps_usd": 1.0,
        "bid_depth_25bps_usd": 1.0,
        "ask_depth_25bps_usd": 1.0,
        "two_sided_depth_25bps_usd": 1.0,
    })
    names: set[str] = set()
    for _ in range(3):
        store.write_book(row)
        store.book.rotate_now()
        closed = store.book.take_closed_paths()
        assert closed
        names.add(closed[0].name)
    assert len(names) == 3


# TEST D — same GCS parquet key cannot be overwritten
def test_gcs_parquet_key_immutable(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    src = tmp_path / "a.parquet"
    src.write_bytes(b"a")
    key = "lighter-mm/runs/run1/books/date=2026-08-09/hour=10/part-x.parquet"
    be.upload_file(src, key, if_generation_match=0)
    src2 = tmp_path / "b.parquet"
    src2.write_bytes(b"b")
    try:
        be.upload_file(src2, key, if_generation_match=0)
        raise AssertionError("expected FileExistsError")
    except FileExistsError:
        pass


# TEST E — cloud collector resume does not hydrate
def test_collector_resume_does_not_hydrate() -> None:
    src = inspect.getsource(CollectorApp.run)
    assert "hydrate_run_parquets" not in src


# TEST F — periodic sync does not analyze
def test_collector_sync_does_not_analyze() -> None:
    src = inspect.getsource(CollectorApp._durable_sync_loop)
    assert "analyze_window" not in src
    assert "_sync_only" in inspect.getsource(CollectorApp)


# TEST G — shutdown does not analyze
def test_collector_shutdown_does_not_analyze() -> None:
    src = inspect.getsource(CollectorApp.shutdown)
    assert "analyze_window" not in src
    assert "build_dashboard_payload" not in src


# TEST H — collector publishes collector_status.json
def test_collector_publishes_collector_status(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.run_id = "abc"
    app.state = RunState(run_id="abc", started_at=now_iso(), status="running")
    app.sync = DurableSync(app.backend, run_id="abc", gcs_prefix="lighter-mm")
    app._ws = None
    app._last_usable_book_sample_ts = None
    app._last_book_row_written_ts = None
    app._trades_without_reference_mid = 0
    app._publish_collector_status()
    payload = app.backend.download_json("lighter-mm/public/collector_status.json")
    assert payload is not None
    assert payload["run_id"] == "abc"
    assert payload["status"] in {"COLLECTING", "DEGRADED", "STALE", "OFFLINE", "COMPLETED"}


# TEST I — collector does not write ranking JSON
def test_collector_does_not_write_ranking_json(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.run_id = "abc"
    app.state = RunState(run_id="abc", started_at=now_iso(), status="running")
    app.sync = DurableSync(app.backend, run_id="abc", gcs_prefix="lighter-mm")
    app._ws = None
    app._last_usable_book_sample_ts = None
    app._last_book_row_written_ts = None
    app._trades_without_reference_mid = 0
    app._publish_collector_status()
    assert app.backend.download_json("lighter-mm/public/latest.json") is None
    assert app.backend.download_json("lighter-mm/public/markets.json") is None
    assert app.backend.download_json("lighter-mm/public/candidates.json") is None


# TEST J — analyzer uses custom AnalysisSources
def test_analyzer_custom_analysis_sources(tmp_path: Path) -> None:
    mount = tmp_path / "mnt" / "lighter-mm" / "runs" / "run1"
    books = mount / "books"
    books.mkdir(parents=True)
    ts = int(time.time() * 1000)
    _write_book_parquet(books / "date=2026-08-09/hour=10/part-a.parquet", ts)
    settings = Settings(data_dir=tmp_path / "unused", reports_dir=tmp_path / "reports")
    sources = AnalysisSources(books=books, trades=mount / "trades", markouts=mount / "markouts")
    result = analyze_range(
        settings,
        start_ms=ts - 60_000,
        end_ms=ts + 60_000,
        sources=sources,
    )
    assert not result.get("error")
    assert result.get("scored")


# TEST K — analyzer lock conflict exits without second analysis
def test_analyzer_lock_conflict_exits_cleanly(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    lock1 = LeaderLock(be, "lighter-mm/state/analyzer.lock.json", holder_id="a", lease_seconds=120)
    lock2 = LeaderLock(be, "lighter-mm/state/analyzer.lock.json", holder_id="b", lease_seconds=120)
    assert lock1.acquire("run1")
    assert not lock2.acquire("run1")


# TEST L — analysis failure does not overwrite prior public results
def test_analysis_failure_preserves_prior_public_results(tmp_path: Path) -> None:
    from lighter_mm.cloud.analyzer import run_cloud_analyze

    settings = Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
    )
    be = LocalStorageBackend(tmp_path / "remote")
    be.upload_json(
        "lighter-mm/public/markets.json",
        {"markets": [{"symbol": "ETH", "score": 90}], "generated_at": now_iso()},
        public=True,
    )
    be.upload_json("lighter-mm/state/active_run.json", {"run_id": "run1", "status": "running"})
    be.upload_json(
        "lighter-mm/runs/run1/state/state.json",
        RunState(run_id="run1", started_at=now_iso(), status="running").to_public_dict(),
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            side_effect=RuntimeError("duckdb boom"),
        ):
            code = run_cloud_analyze(settings)
    assert code == 1
    markets = be.download_json("lighter-mm/public/markets.json")
    assert markets is not None
    assert markets["markets"][0]["symbol"] == "ETH"


# TEST M — completed run creates final analysis request
def test_completed_run_creates_final_analysis_request(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    app = CollectorApp.__new__(CollectorApp)
    app.settings = settings
    app.backend = LocalStorageBackend(tmp_path / "remote")
    app.run_id = "run72"
    app.state = RunState(run_id="run72", started_at=now_iso(), status="running")
    app.sync = DurableSync(app.backend, run_id="run72", gcs_prefix="lighter-mm")
    app._create_final_analysis_request()
    req = app.backend.download_json("lighter-mm/analysis-requests/run72.json")
    assert req is not None
    assert req["status"] == "pending"
    assert req["type"] == "final"


# TEST N — pending final run selected before active running run
def test_pending_final_run_selected_first(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    settings = Settings(gcs_prefix="lighter-mm")
    be.upload_json(
        "lighter-mm/analysis-requests/final-run.json",
        {"run_id": "final-run", "type": "final", "status": "pending", "requested_at": "2026-01-01"},
    )
    be.upload_json(
        "lighter-mm/state/active_run.json",
        {"run_id": "active-run", "status": "running"},
    )
    selected = select_run_to_analyze(be, settings)
    assert selected == ("final-run", "final")


# TEST O — dashboard separates collector and analysis health (source check)
def test_dashboard_separates_collector_and_analysis_health() -> None:
    data_ts = Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "data.ts"
    text = data_ts.read_text(encoding="utf-8")
    assert "effectiveCollectorStatus" in text
    assert "effectiveAnalysisStatus" in text
    assert "getCollectorStatusResult" in text
    assert "getAnalysisStatusResult" in text


# TEST — end-to-end collector sync → analyzer read
def test_e2e_collector_sync_analyzer_read(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path / "remote")
    data_root = tmp_path / "hot"
    data_root.mkdir()
    sync = DurableSync(be, run_id="e2e", gcs_prefix="lighter-mm")
    local = data_root / "book_samples/date=2026-08-09/hour=10/part-e2e.parquet"
    ts = int(time.time() * 1000)
    _write_book_parquet(local, ts)
    sync.upload_new_parquets(data_root, paths=[local])
    assert not local.exists()

    mount = tmp_path / "mnt" / "lighter-mm" / "runs" / "e2e"
    remote_books = mount / "books"
    remote_books.mkdir(parents=True)
    raw = be.download_bytes(
        "lighter-mm/runs/e2e/books/date=2026-08-09/hour=10/part-e2e.parquet"
    )
    assert raw
    dest = remote_books / "date=2026-08-09/hour=10/part-e2e.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    settings = Settings(data_dir=data_root, reports_dir=tmp_path / "reports")
    sources = AnalysisSources(
        books=remote_books,
        trades=mount / "trades",
        markouts=mount / "markouts",
    )
    result = analyze_range(
        settings,
        start_ms=ts - 60_000,
        end_ms=ts + 60_000,
        sources=sources,
    )
    assert not result.get("error")
    assert result.get("scored")


def test_build_collector_status_payload_shape() -> None:
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        samples_written=10,
        trades_written=2,
        markouts_written=1,
    )
    payload = build_collector_status_payload(state, settings=Settings())
    assert payload["samples_written"] == 10
    assert "last_successful_sync" in payload
    assert "ws" in payload
