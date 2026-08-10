"""Analyzer RUNNING heartbeat + race-safe final status tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lighter_mm.cloud import analyzer_publish
from lighter_mm.cloud.analysis_outcome import is_stale_running, running_reference_timestamp
from lighter_mm.cloud.analyzer import run_cloud_analyze
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.state import RunState, now_iso


def _base_settings(tmp_path: Path, **overrides) -> Settings:
    renew = float(overrides.pop("analyzer_lock_renew_interval_seconds", 60.0))
    kwargs = dict(
        environment="local",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        gcs_prefix="lighter-mm",
        analyzer_mount_path=tmp_path / "mnt",
        git_sha="abc123",
        analyzer_version="0.1.0",
        analysis_stale_minutes=30.0,
        analyzer_lock_renew_interval_seconds=max(renew, 10.0),
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    # Tests may use sub-second renew cadence; Field ge=10 applies at construction only.
    settings.analyzer_lock_renew_interval_seconds = renew
    return settings


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


def _analyze_ok(*_a, **_k):
    return {
        "scored": [],
        "book_row_count": 1,
        "trade_row_count": 0,
        "markout_row_count": 0,
    }


def _payload_ok(*_a, **_k):
    return {
        "latest": {"generated_at": now_iso()},
        "markets": [],
        "candidates": [],
        "market_details": {},
    }


def test_heartbeat_fresh_not_stale_despite_old_started_at() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    status = {
        "status": "RUNNING",
        "started_at": (now - timedelta(minutes=60)).isoformat(),
        "generated_at": (now - timedelta(minutes=60)).isoformat(),
        "heartbeat_at": (now - timedelta(minutes=1)).isoformat(),
    }
    assert running_reference_timestamp(status) == status["heartbeat_at"]
    assert is_stale_running(status, stale_minutes=30.0, now=now) is False


def test_heartbeat_stale_when_heartbeat_old() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    status = {
        "status": "RUNNING",
        "started_at": (now - timedelta(minutes=60)).isoformat(),
        "heartbeat_at": (now - timedelta(minutes=31)).isoformat(),
    }
    assert is_stale_running(status, stale_minutes=30.0, now=now) is True


def test_legacy_running_falls_back_to_started_at() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    status = {
        "status": "RUNNING",
        "started_at": (now - timedelta(minutes=31)).isoformat(),
        "generated_at": (now - timedelta(minutes=31)).isoformat(),
    }
    assert running_reference_timestamp(status) == status["started_at"]
    assert is_stale_running(status, stale_minutes=30.0, now=now) is True


def test_final_ok_not_overwritten_by_late_heartbeat(tmp_path: Path) -> None:
    """Stop heartbeat before final publish; late loop must not rewrite RUNNING."""
    settings = _base_settings(tmp_path, analyzer_lock_renew_interval_seconds=0.05)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    statuses: list[str] = []
    real_publish = analyzer_publish._publish_analysis_status

    def tracking_publish(*args, **kwargs):
        statuses.append(str(kwargs.get("status")))
        return real_publish(*args, **kwargs)

    def slow_analyze(*_a, **_k):
        time.sleep(0.2)
        return _analyze_ok()

    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.analyze_range", side_effect=slow_analyze):
            with patch(
                "lighter_mm.cloud.analyzer.build_dashboard_payload",
                side_effect=_payload_ok,
            ):
                with patch(
                    "lighter_mm.cloud.analyzer._publish_analysis_status",
                    side_effect=tracking_publish,
                ):
                    code = run_cloud_analyze(settings)
    assert code == 0
    assert statuses
    last_terminal = max(i for i, s in enumerate(statuses) if s in {"OK", "DEGRADED", "ERROR"})
    assert statuses[last_terminal] in {"OK", "DEGRADED"}
    assert all(s != "RUNNING" for s in statuses[last_terminal + 1 :])
    # Give any stray thread a moment; status must remain terminal.
    time.sleep(0.15)
    final = be.download_json("lighter-mm/public/analysis_status.json")
    assert final["status"] in {"OK", "DEGRADED"}


def test_heartbeat_write_failure_is_warning_analysis_continues(tmp_path: Path, caplog) -> None:
    settings = _base_settings(tmp_path, analyzer_lock_renew_interval_seconds=0.05)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    calls = {"n": 0}
    real_publish = analyzer_publish._publish_analysis_status

    def flaky_publish(*args, **kwargs):
        calls["n"] += 1
        if kwargs.get("status") == "RUNNING" and calls["n"] > 1:
            raise RuntimeError("gcs heartbeat write failed")
        return real_publish(*args, **kwargs)

    def slow_analyze(*_a, **_k):
        time.sleep(0.15)
        return _analyze_ok()

    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.analyze_range", side_effect=slow_analyze):
            with patch(
                "lighter_mm.cloud.analyzer.build_dashboard_payload",
                side_effect=_payload_ok,
            ):
                with patch(
                    "lighter_mm.cloud.analyzer._publish_analysis_status",
                    side_effect=flaky_publish,
                ):
                    code = run_cloud_analyze(settings)
    assert code == 0
    assert "heartbeat publish failed" in caplog.text
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] in {"OK", "DEGRADED"}


def test_lock_renewal_failure_stops_analysis(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path, analyzer_lock_renew_interval_seconds=0.05)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)

    def slow_analyze(*_a, **_k):
        time.sleep(0.2)
        return _analyze_ok()

    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.LeaderLock.renew", return_value=False):
            with patch("lighter_mm.cloud.analyzer.analyze_range", side_effect=slow_analyze):
                with patch(
                    "lighter_mm.cloud.analyzer.build_dashboard_payload",
                    side_effect=_payload_ok,
                ):
                    code = run_cloud_analyze(settings)
    assert code == 1


def test_blocked_heartbeat_cannot_overwrite_terminal_status(tmp_path: Path) -> None:
    """In-flight RUNNING GCS write must finish before terminal; never overwrite after."""
    import threading

    settings = _base_settings(tmp_path, analyzer_lock_renew_interval_seconds=0.05)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    statuses: list[str] = []
    heartbeat_entered = threading.Event()
    allow_heartbeat_finish = threading.Event()
    real_publish = analyzer_publish._publish_analysis_status
    running_seen = {"n": 0}

    def blocking_publish(*args, **kwargs):
        status = str(kwargs.get("status"))
        if status == "RUNNING":
            running_seen["n"] += 1
            if running_seen["n"] > 1:
                # Subsequent heartbeat holds the publish lock while blocked.
                heartbeat_entered.set()
                assert allow_heartbeat_finish.wait(timeout=30)
        statuses.append(status)
        return real_publish(*args, **kwargs)

    def slow_analyze(*_a, **_k):
        assert heartbeat_entered.wait(timeout=10)
        time.sleep(0.05)
        return _analyze_ok()

    def releaser() -> None:
        assert heartbeat_entered.wait(timeout=10)
        # Let analyze finish and terminal publish wait on the status lock.
        time.sleep(0.25)
        allow_heartbeat_finish.set()

    threading.Thread(target=releaser, name="hb-releaser", daemon=True).start()

    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch("lighter_mm.cloud.analyzer.analyze_range", side_effect=slow_analyze):
            with patch(
                "lighter_mm.cloud.analyzer.build_dashboard_payload",
                side_effect=_payload_ok,
            ):
                with patch(
                    "lighter_mm.cloud.analyzer._publish_analysis_status",
                    side_effect=blocking_publish,
                ):
                    code = run_cloud_analyze(settings)
    assert code == 0
    assert statuses
    assert statuses[-1] in {"OK", "DEGRADED"}
    last_terminal = max(i for i, s in enumerate(statuses) if s in {"OK", "DEGRADED", "ERROR"})
    assert all(s != "RUNNING" for s in statuses[last_terminal + 1 :])
    time.sleep(0.2)
    final = be.download_json("lighter-mm/public/analysis_status.json")
    assert final["status"] in {"OK", "DEGRADED"}


def test_lock_renewal_continues_while_heartbeat_blocked(tmp_path: Path) -> None:
    """Heartbeat GCS delay must not stall LeaderLock renewal."""
    import threading

    settings = _base_settings(tmp_path, analyzer_lock_renew_interval_seconds=0.05)
    be = LocalStorageBackend(tmp_path / "remote")
    _seed_run(be)
    heartbeat_entered = threading.Event()
    allow_heartbeat_finish = threading.Event()
    renew_times: list[float] = []
    real_publish = analyzer_publish._publish_analysis_status
    running_seen = {"n": 0}

    def blocking_publish(*args, **kwargs):
        status = str(kwargs.get("status"))
        if status == "RUNNING":
            running_seen["n"] += 1
            if running_seen["n"] > 1:
                heartbeat_entered.set()
                assert allow_heartbeat_finish.wait(timeout=30)
        return real_publish(*args, **kwargs)

    def tracking_renew(self, *args, **kwargs):
        renew_times.append(time.time())
        return True

    def slow_analyze(*_a, **_k):
        assert heartbeat_entered.wait(timeout=10)
        # Hold analysis while renewals continue despite blocked heartbeat.
        time.sleep(0.35)
        allow_heartbeat_finish.set()
        return _analyze_ok()

    with patch("lighter_mm.cloud.analyzer.build_storage_backend", return_value=be):
        with patch(
            "lighter_mm.cloud.analyzer.LeaderLock.renew",
            autospec=True,
            side_effect=tracking_renew,
        ):
            with patch("lighter_mm.cloud.analyzer.analyze_range", side_effect=slow_analyze):
                with patch(
                    "lighter_mm.cloud.analyzer.build_dashboard_payload",
                    side_effect=_payload_ok,
                ):
                    with patch(
                        "lighter_mm.cloud.analyzer._publish_analysis_status",
                        side_effect=blocking_publish,
                    ):
                        code = run_cloud_analyze(settings)
    assert code == 0
    assert len(renew_times) >= 2
    status = be.download_json("lighter-mm/public/analysis_status.json")
    assert status["status"] in {"OK", "DEGRADED"}
