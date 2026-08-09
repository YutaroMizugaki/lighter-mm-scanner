"""Analyzer analysis_status.json lifecycle: RUNNING → OK / ERROR."""

from __future__ import annotations

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
    )


def _seed_run(be: LocalStorageBackend, run_id: str = "run1") -> None:
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


def test_analyzer_success_sets_last_successful_analysis_at(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    scored_obj = type("Scored", (), {"candidate": True})()
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            return_value={
                "scored": [scored_obj],
                "book_row_count": 10,
                "trade_row_count": 5,
                "markout_row_count": 3,
            },
        ):
            with patch("lighter_mm.cloud.analyzer.build_dashboard_payload") as mock_payload:
                mock_payload.return_value = {
                    "latest": {"generated_at": now_iso()},
                    "markets": [],
                    "candidates": [],
                    "market_details": {},
                }
                code = run_cloud_analyze(settings)
    assert code == 0
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status is not None
    assert status["status"] == "OK"
    assert status["last_successful_analysis_at"] == status["generated_at"]
    assert status.get("duration_seconds") is not None


def test_analyzer_failure_preserves_last_successful_analysis_at(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    prior_ts = "2026-08-09T10:00:00Z"
    be.upload_json(
        "lighter-mm/public/analysis_status.json",
        {
            "status": "OK",
            "run_id": "run1",
            "generated_at": prior_ts,
            "last_successful_analysis_at": prior_ts,
        },
        public=True,
    )
    _seed_run(be)
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            side_effect=RuntimeError("duckdb boom"),
        ):
            code = run_cloud_analyze(settings)
    assert code == 1
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status is not None
    assert status["status"] == "ERROR"
    assert status["last_successful_analysis_at"] == prior_ts
    assert "duckdb boom" in status["error"]


def test_analyzer_publishes_running_before_analysis(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.analyze_range",
            return_value={
                "scored": [],
                "book_row_count": 0,
                "trade_row_count": 0,
                "markout_row_count": 0,
            },
        ):
            with patch("lighter_mm.cloud.analyzer.build_dashboard_payload") as mock_payload:
                mock_payload.return_value = {
                    "latest": {"generated_at": now_iso()},
                    "markets": [],
                    "candidates": [],
                    "market_details": {},
                }
                with patch("lighter_mm.cloud.analyzer._publish_analysis_status") as mock_publish:
                    run_cloud_analyze(settings)
                    statuses = [c.kwargs["status"] for c in mock_publish.call_args_list]
    assert "RUNNING" in statuses
    assert "OK" in statuses
