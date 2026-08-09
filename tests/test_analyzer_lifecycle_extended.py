"""Analyzer lifecycle statuses when no run, lock busy, or failure."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from lighter_mm.cloud.analyzer import run_cloud_analyze
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.state import RunState, now_iso


def _base_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
        git_sha="abc123",
        analyzer_version="0.1.0",
    )


def _seed_run(be: LocalStorageBackend, run_id: str = "run1") -> None:
    be.upload_json("lighter-mm/state/active_run.json", {"run_id": run_id, "status": "running"})
    now = now_iso()
    be.upload_json(
        f"lighter-mm/runs/{run_id}/state/state.json",
        RunState(
            run_id=run_id,
            started_at=now,
            status="running",
            last_successful_flush=now,
            last_durable_event_ms=int(datetime.now(UTC).timestamp() * 1000) - 60_000,
        ).to_public_dict(),
    )


def test_no_active_run_publishes_status(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        code = run_cloud_analyze(settings)
    assert code == 0
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status is not None
    assert status["status"] == "NOT_STARTED"
    assert status["git_sha"] == "abc123"
    assert status["analyzer_version"] == "0.1.0"


def test_no_active_run_after_prior_ok(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    be.upload_json(
        "lighter-mm/public/analysis_status.json",
        {
            "status": "OK",
            "run_id": "run1",
            "generated_at": "2026-08-09T10:00:00Z",
            "last_successful_analysis_at": "2026-08-09T10:00:00Z",
        },
        public=True,
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        code = run_cloud_analyze(settings)
    assert code == 0
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] == "NO_ACTIVE_RUN"
    assert status["last_successful_analysis_at"] == "2026-08-09T10:00:00Z"


def test_lock_busy_does_not_overwrite_running(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    be.upload_json(
        "lighter-mm/public/analysis_status.json",
        {
            "status": "RUNNING",
            "run_id": "run1",
            "generated_at": "2026-08-09T10:00:00Z",
            "started_at": "2026-08-09T10:00:00Z",
        },
        public=True,
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.LeaderLock.acquire", return_value=False):
            code = run_cloud_analyze(settings)
    assert code == 0
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] == "RUNNING"


def test_analyzer_failure_publishes_error(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            side_effect=RuntimeError("analysis boom"),
        ):
            code = run_cloud_analyze(settings)
    assert code == 1
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] == "ERROR"
    assert "analysis boom" in status["error"]
