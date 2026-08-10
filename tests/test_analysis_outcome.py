"""Regression tests for analysis success criteria and stale RUNNING detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lighter_mm.cloud.analysis_outcome import is_analysis_success, is_stale_running
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
        analysis_stale_minutes=30.0,
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


def test_stale_running_detected_after_analysis_stale_minutes() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    status = {
        "status": "RUNNING",
        "run_id": "run1",
        "started_at": (now - timedelta(minutes=31)).isoformat(),
        "generated_at": (now - timedelta(minutes=31)).isoformat(),
    }
    assert is_stale_running(status, stale_minutes=30.0, now=now) is True


def test_active_running_not_stale() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    status = {
        "status": "RUNNING",
        "run_id": "run1",
        "started_at": (now - timedelta(minutes=10)).isoformat(),
        "generated_at": (now - timedelta(minutes=10)).isoformat(),
    }
    assert is_stale_running(status, stale_minutes=30.0, now=now) is False


def test_ok_without_current_json_is_not_success() -> None:
    status = {
        "status": "OK",
        "last_successful_analysis_at": "2026-08-10T11:00:00+00:00",
    }
    assert is_analysis_success(status, None) is False


def test_running_with_missing_current_json_is_not_success() -> None:
    status = {
        "status": "RUNNING",
        "started_at": "2026-08-10T11:00:00+00:00",
        "last_successful_analysis_at": None,
    }
    assert is_analysis_success(status, None) is False
    assert is_analysis_success(status, {"analysis_id": "x"}) is False


def test_ok_with_last_successful_and_current_is_success() -> None:
    status = {
        "status": "OK",
        "last_successful_analysis_at": "2026-08-10T11:00:00+00:00",
    }
    assert is_analysis_success(status, {"analysis_id": "gen1"}) is True


def test_degraded_with_last_successful_and_current_is_success() -> None:
    status = {
        "status": "DEGRADED",
        "last_successful_analysis_at": "2026-08-10T11:00:00+00:00",
    }
    assert is_analysis_success(status, {"analysis_id": "gen1"}) is True


def test_lock_busy_active_running_does_not_start_dual_analysis(
    tmp_path: Path, caplog
) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    started = datetime.now(UTC).isoformat()
    be.upload_json(
        "lighter-mm/public/analysis_status.json",
        {
            "status": "RUNNING",
            "run_id": "run1",
            "generated_at": started,
            "started_at": started,
        },
        public=True,
    )
    be.upload_json(
        "lighter-mm/state/analyzer.lock.json",
        {
            "holder_id": "other-holder",
            "run_id": "run1",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=20)).isoformat(),
        },
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.LeaderLock.acquire", return_value=False):
            with patch("lighter_mm.cloud.analyzer.analyze_range") as mock_analyze:
                code = run_cloud_analyze(settings)
    assert code == 0
    mock_analyze.assert_not_called()
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] == "RUNNING"
    assert "stale_running=False" in caplog.text or "already running" in caplog.text


def test_lock_busy_stale_running_logs_diagnostics_without_dual_analysis(
    tmp_path: Path, caplog
) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    stale_started = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    be.upload_json(
        "lighter-mm/public/analysis_status.json",
        {
            "status": "RUNNING",
            "run_id": "run1",
            "generated_at": stale_started,
            "started_at": stale_started,
        },
        public=True,
    )
    be.upload_json(
        "lighter-mm/state/analyzer.lock.json",
        {
            "holder_id": "dead-holder",
            "run_id": "run1",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
    )
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.LeaderLock.acquire", return_value=False):
            with patch("lighter_mm.cloud.analyzer.analyze_range") as mock_analyze:
                code = run_cloud_analyze(settings)
    assert code == 0
    mock_analyze.assert_not_called()
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] == "RUNNING"
    assert "stale_running=True" in caplog.text
    assert "dead-holder" in caplog.text
    assert "stale RUNNING with analyzer lock busy" in caplog.text
