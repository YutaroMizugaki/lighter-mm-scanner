"""Local storage backend, leader lock, and dashboard payload tests."""

from __future__ import annotations

from pathlib import Path

from lighter_mm.cloud.dashboard_data import build_dashboard_payload, collector_status_label
from lighter_mm.cloud.estimate import estimate_storage
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.lock import LeaderLock
from lighter_mm.storage.state import RunState, now_iso


def test_local_backend_roundtrip(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    be.upload_json("lighter-mm/state/active_run.json", {"run_id": "abc", "status": "running"})
    got = be.download_json("lighter-mm/state/active_run.json")
    assert got and got["run_id"] == "abc"
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    uri = be.upload_file(p, "lighter-mm/runs/abc/books/date=2026-08-09/hour=07/part.parquet")
    assert uri.startswith("file://")
    assert be.exists("lighter-mm/runs/abc/books/date=2026-08-09/hour=07/part.parquet")


def test_leader_lock_exclusive(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    a = LeaderLock(be, "lighter-mm/state/leader.lock.json", holder_id="a", lease_seconds=60)
    b = LeaderLock(be, "lighter-mm/state/leader.lock.json", holder_id="b", lease_seconds=60)
    assert a.acquire("run1") is True
    assert b.acquire("run1") is False
    a.release()
    # expired/released allows takeover
    assert b.acquire("run1") is True


def test_status_label_and_estimate() -> None:
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
    )
    assert collector_status_label(state, ok_minutes=20, warn_minutes=40) == "COLLECTING"
    est = estimate_storage(bytes_so_far=10 * 1024 * 1024, elapsed_hours=0.1)
    assert est["mb_per_hour"] > 0
    assert est["estimate_72h"]["gb"] > 0


def test_dashboard_payload_handles_empty(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    (settings.data_dir / "book_samples").mkdir(parents=True)
    payload = build_dashboard_payload(settings, hours=1, state=None)
    assert payload["latest"]["title"] == "Lighter MM Scanner"
    assert payload["markets"] == []
