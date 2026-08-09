"""Parquet corruption resilience: skip corrupt files, degraded analysis, atomic writes."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_window
from lighter_mm.cloud.analyzer import run_cloud_analyze
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_store import ParquetStore, _book_schema
from lighter_mm.storage.parquet_validation import (
    discover_parquet_files,
    is_analysis_candidate,
    prepare_parquet_dataset,
    validate_parquet_file,
)
from lighter_mm.storage.state import RunState, now_iso
from tests.helpers import enrich_book_row


def _book_row(ts: int) -> dict:
    return enrich_book_row({
        "timestamp_ms": ts,
        "market_id": 1,
        "symbol": "ETH",
        "best_bid": 100.0,
        "best_ask": 100.1,
        "mid": 100.05,
        "spread_absolute": 0.1,
        "spread_bps": 10.0,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 100.0,
        "best_ask_size_usd": 100.1,
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
        "bid_depth_5bps_usd": 50.0,
        "ask_depth_5bps_usd": 50.0,
        "two_sided_depth_5bps_usd": 50.0,
        "bid_depth_10bps_usd": 200.0,
        "ask_depth_10bps_usd": 200.0,
        "two_sided_depth_10bps_usd": 200.0,
        "bid_depth_25bps_usd": 400.0,
        "ask_depth_25bps_usd": 400.0,
        "two_sided_depth_25bps_usd": 400.0,
    })


def _write_valid_parquet(path: Path, ts: int) -> None:
    schema = _book_schema([5, 10, 25])
    table = pa.Table.from_pylist([_book_row(ts)], schema=schema)
    pq.write_table(table, path)


def _seed_corrupt_parquet(root: Path, name: str = "part-bad.parquet") -> Path:
    nested = root / "date=2026-08-09" / "hour=14"
    nested.mkdir(parents=True, exist_ok=True)
    bad = nested / name
    bad.write_bytes(b"not-a-parquet-file")
    return bad


def test_healthy_parquet_only(tmp_path: Path) -> None:
    """Test 1: healthy files only → status healthy, analysis succeeds."""
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))
    store.close()

    result = analyze_window(settings, hours=1.0)
    assert result.get("error") is None
    assert len(result["scored"]) >= 1
    health = result["parquet_health"]
    assert health["status"] == "healthy"
    assert health["corrupt_parquet_files"] == 0


def test_corrupt_parquet_skipped_degraded(tmp_path: Path) -> None:
    """Test 2: one corrupt file → degraded, analysis continues."""
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))
    store.close()
    bad = _seed_corrupt_parquet(tmp_path / "book_samples")

    result = analyze_window(settings, hours=1.0)
    assert result.get("error") is None
    assert len(result["scored"]) >= 1
    health = result["parquet_health"]
    assert health["status"] == "degraded"
    assert health["corrupt_parquet_files"] == 1
    assert any("part-bad" in e["path"] for e in health["skipped_files"])
    assert not bad.exists()
    assert (tmp_path / "book_samples/quarantine").exists()


def test_zero_byte_parquet_skipped(tmp_path: Path) -> None:
    """Test 3: 0-byte parquet → skip, analysis continues."""
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))
    store.close()
    empty = tmp_path / "book_samples/date=2026-08-09/hour=14/part-empty.parquet"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")

    result = analyze_window(settings, hours=1.0)
    assert result.get("error") is None
    health = result["parquet_health"]
    assert health["corrupt_parquet_files"] == 1
    assert "empty" in health["skipped_files"][0]["error"].lower()


def test_tmp_file_not_analyzed(tmp_path: Path) -> None:
    """Test 4: .tmp files are not analysis candidates."""
    root = tmp_path / "book_samples/date=2026-08-09/hour=14"
    root.mkdir(parents=True)
    tmp_file = root / "part-20260809_1415-abc.parquet.tmp"
    _write_valid_parquet(tmp_file, int(time.time() * 1000))

    assert not is_analysis_candidate(tmp_file)
    discovered = discover_parquet_files(tmp_path / "book_samples")
    assert tmp_file not in discovered


def test_all_parquet_corrupt_failed_status(tmp_path: Path) -> None:
    """Test 5: all corrupt → failed status with explicit error."""
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    books = tmp_path / "book_samples"
    _seed_corrupt_parquet(books, "part-only-bad.parquet")

    result = analyze_window(settings, hours=1.0)
    assert result.get("error")
    health = result["parquet_health"]
    assert health["status"] == "failed"
    assert health["valid_parquet_files"] == 0
    assert health["corrupt_parquet_files"] == 1


def test_atomic_write_no_final_on_validation_failure(tmp_path: Path) -> None:
    """Test 6: validation failure leaves no finalized .parquet (tmp removed)."""
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts))

    with patch(
        "lighter_mm.storage.parquet_store.validate_parquet_file",
        return_value=(False, "simulated validation failure"),
    ):
        store.close()

    finals = list((tmp_path / "book_samples").glob("date=*/hour=*/*.parquet"))
    temps = list((tmp_path / "book_samples").rglob("*.parquet.tmp"))
    assert finals == []
    assert temps == []


def test_validate_parquet_detects_truncated_file(tmp_path: Path) -> None:
    """Reproduce 'No magic bytes' style corruption detection."""
    path = tmp_path / "truncated.parquet"
    path.write_bytes(b"PAR1" + b"\x00" * 20)
    ok, err = validate_parquet_file(path)
    assert not ok
    assert err


def test_analyzer_publishes_degraded_status(tmp_path: Path) -> None:
    """Analyzer publishes DEGRADED analysis_status when corrupt files skipped."""
    settings = Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
    )
    be = LocalStorageBackend(tmp_path / "remote")
    run_id = "run1"
    be.upload_json("lighter-mm/state/active_run.json", {"run_id": run_id, "status": "running"})
    be.upload_json(
        f"lighter-mm/runs/{run_id}/state/state.json",
        RunState(
            run_id=run_id,
            started_at=now_iso(),
            status="running",
            last_successful_flush=now_iso(),
        ).to_public_dict(),
    )
    scored_obj = type("Scored", (), {"candidate": True})()
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            return_value={
                "scored": [scored_obj],
                "book_row_count": 10,
                "trade_row_count": 0,
                "markout_row_count": 0,
                "parquet_health": {
                    "status": "degraded",
                    "valid_parquet_files": 10,
                    "corrupt_parquet_files": 1,
                    "skipped_files": [
                        {
                            "path": "books/date=2026-08-09/hour=14/part-bad.parquet",
                            "error": "No magic bytes found at end of file",
                        }
                    ],
                },
            },
        ):
            with patch("lighter_mm.cloud.analyzer.build_dashboard_payload") as mock_payload:
                mock_payload.return_value = {
                    "latest": {"generated_at": now_iso(), "status": "DEGRADED"},
                    "markets": [],
                    "candidates": [],
                    "market_details": {},
                }
                code = run_cloud_analyze(settings)
    assert code == 0
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status is not None
    assert status["status"] == "DEGRADED"
    assert status["corrupt_parquet_files"] == 1
    assert status["skipped_files"][0]["error"] == "No magic bytes found at end of file"


def test_prepare_parquet_quarantines_corrupt(tmp_path: Path) -> None:
    root = tmp_path / "books"
    bad = _seed_corrupt_parquet(root)
    valid_path = root / "date=2026-08-09" / "hour=10" / "part-good.parquet"
    valid_path.parent.mkdir(parents=True, exist_ok=True)
    _write_valid_parquet(valid_path, int(time.time() * 1000))

    valid, corrupt = prepare_parquet_dataset(root)
    assert len(valid) == 1
    assert len(corrupt) == 1
    assert not bad.exists()
    quarantined = root / "quarantine/date=2026-08-09/hour=14/part-bad.parquet"
    assert quarantined.exists()
