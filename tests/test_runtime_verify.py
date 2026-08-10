"""Unit tests for GCP runtime verification (no live GCP)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from lighter_mm.runtime_verify.models import (
    AnalyzerJobInfo,
    ExecutionInfo,
    ParquetBookInfo,
    RuntimeSnapshot,
    SchedulerInfo,
    WorkerPoolInfo,
)
from lighter_mm.runtime_verify.render import render_json
from lighter_mm.runtime_verify.verifier import verify_runtime


def _now() -> datetime:
    return datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


def _base_snapshot(**kwargs) -> RuntimeSnapshot:
    now = _now()
    defaults: dict = {
        "expected_git_sha": "abc123",
        "expected_sha_source": "test",
        "worker_pool": WorkerPoolInfo(
            exists=True,
            image=".../collector:abc123",
            git_sha="abc123",
            instances=1,
        ),
        "analyzer_job": AnalyzerJobInfo(
            exists=True,
            image=".../collector:abc123",
            git_sha="abc123",
            command=["lighter-mm"],
            args=["cloud-analyze"],
        ),
        "scheduler": SchedulerInfo(exists=True, target_uri="jobs/lighter-mm-analyzer:run"),
        "collector_status": {
            "run_id": "run-current",
            "status": "OK",
            "generated_at": now.isoformat(),
            "git_sha": "abc123",
            "last_successful_sync": (now - timedelta(seconds=52)).isoformat(),
            "last_durable_event_at": (now - timedelta(seconds=35)).isoformat(),
            "last_sync_error": None,
            "consecutive_sync_failures": 0,
            "ws": {
                "connected_shards": 3,
                "total_shards": 3,
                "planned_channels": 288,
                "acked_channels": 288,
                "subscribed_channels": 288,
            },
        },
        "collector_status_raw": "{}",
        "analysis_status": {
            "status": "OK",
            "run_id": "run-current",
            "generated_at": (now - timedelta(minutes=6)).isoformat(),
            "git_sha": "abc123",
            "last_successful_analysis_at": (now - timedelta(minutes=6)).isoformat(),
            "valid_parquet_files": 10,
            "corrupt_parquet_files": 0,
            "parquet_health_status": "ok",
        },
        "analysis_status_raw": "{}",
        "current_json": {
            "analysis_id": "gen1",
            "generated_at": now.isoformat(),
        },
        "current_json_raw": "{}",
        "public_prefix": "lighter-mm/public",
        "generation_files": {
            "lighter-mm/public/generations/gen1/latest.json": json.dumps(
                {
                    "run_id": "run-current",
                    "generated_at": now.isoformat(),
                    "markets": [
                        {
                            "market_id": 1,
                            "symbol": "ETH",
                            "score": 80.0,
                            "data_coverage_pct": 95.0,
                            "observation_coverage_pct": 95.0,
                            "usable_quote_coverage_pct": 90.0,
                            "spread_coverage_pct": 88.0,
                        }
                    ],
                }
            ),
            "lighter-mm/public/generations/gen1/markets.json": json.dumps(
                {
                    "markets": [
                        {
                            "market_id": 1,
                            "symbol": "ETH",
                            "score": 80.0,
                            "data_coverage_pct": 95.0,
                            "observation_coverage_pct": 95.0,
                            "usable_quote_coverage_pct": 90.0,
                            "spread_coverage_pct": 88.0,
                        }
                    ]
                }
            ),
        },
        "parquet_book": ParquetBookInfo(
            object_path="lighter-mm/runs/run/books/part.parquet",
            updated_at=now - timedelta(seconds=42),
            size_bytes=1024,
            max_timestamp_ms=int((now - timedelta(seconds=35)).timestamp() * 1000),
            row_count=1823,
            valid=True,
        ),
        "executions": [
            ExecutionInfo(
                name="exec-1",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=8),
                succeeded=True,
            )
        ],
        "dashboard_http_status": 200,
        "dashboard_body": "Lighter MM Scanner",
    }
    defaults.update(kwargs)
    return RuntimeSnapshot(**defaults)


def test_healthy_all_components_exit_0() -> None:
    report = verify_runtime(_base_snapshot(), now=_now())
    assert report.exit_code() == 0
    assert report.status == "healthy"


def test_sync_fresh_durable_stale_fails() -> None:
    now = _now()
    snap = _base_snapshot(
        collector_status={
            "run_id": "run1",
            "status": "OK",
            "generated_at": now.isoformat(),
            "git_sha": "abc123",
            "last_successful_sync": (now - timedelta(minutes=2)).isoformat(),
            "last_durable_event_at": (now - timedelta(minutes=60)).isoformat(),
            "ws": {"connected_shards": 1, "total_shards": 1, "planned_channels": 1, "acked_channels": 1, "subscribed_channels": 1},
        }
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 1
    assert any(c.name == "durable_data_age" and c.level.value == "FAIL" for c in report.checks)


def test_collector_status_malformed_fails() -> None:
    snap = _base_snapshot(collector_status=None, collector_status_raw="{bad")
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_collector_revision_mismatch_fails() -> None:
    now = _now()
    base_c = _base_snapshot().collector_status or {}
    snap = _base_snapshot(
        collector_status={**base_c, "git_sha": "oldsha"},
        worker_pool=WorkerPoolInfo(exists=True, git_sha="abc123", image="x:abc123"),
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 1


def test_latest_book_parquet_missing_fails() -> None:
    snap = _base_snapshot(parquet_book=ParquetBookInfo(error="missing"))
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_parquet_zero_byte_fails() -> None:
    snap = _base_snapshot(
        parquet_book=ParquetBookInfo(object_path="x.parquet", size_bytes=0, error="zero bytes")
    )
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_parquet_corrupt_fails() -> None:
    snap = _base_snapshot(
        parquet_book=ParquetBookInfo(
            object_path="x.parquet",
            size_bytes=100,
            valid=False,
            error="corrupt",
        )
    )
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_analyzer_latest_execution_failed_fails() -> None:
    now = _now()
    snap = _base_snapshot(
        executions=[
            ExecutionInfo(name="e1", failed=True, completed_at=now - timedelta(minutes=5)),
        ]
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 1


def test_analyzer_old_sha_new_revision_warn() -> None:
    now = _now()
    snap = _base_snapshot(
        analysis_status={
            "status": "OK",
            "git_sha": "oldsha",
            "generated_at": (now - timedelta(minutes=10)).isoformat(),
            "last_successful_analysis_at": (now - timedelta(minutes=10)).isoformat(),
        },
        analyzer_job=AnalyzerJobInfo(exists=True, git_sha="abc123", command=["lighter-mm"], args=["cloud-analyze"]),
        executions=[],  # no new execution
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 0
    assert any("has not produced analysis yet" in c.message for c in report.checks)


def test_analyzer_new_revision_cloud_run_complete_stale_status_warns() -> None:
    """Cloud Run COMPLETE must not FAIL revision freshness by itself."""
    now = _now()
    snap = _base_snapshot(
        analysis_status={
            "status": "OK",
            "git_sha": "oldsha",
            "generated_at": now.isoformat(),
            "last_successful_analysis_at": now.isoformat(),
        },
        executions=[
            ExecutionInfo(name="e1", succeeded=True, completed_at=now - timedelta(minutes=5)),
        ],
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 0
    assert any(
        c.name == "analysis.revision" and c.level.value == "WARN" and "has not produced analysis yet" in c.message
        for c in report.checks
    )


def test_analysis_outcome_requires_status_and_current_json() -> None:
    now = _now()
    # RUNNING + missing current.json is not analysis success.
    snap = _base_snapshot(
        analysis_status={
            "status": "RUNNING",
            "git_sha": "abc123",
            "started_at": (now - timedelta(minutes=5)).isoformat(),
            "generated_at": (now - timedelta(minutes=5)).isoformat(),
            "last_successful_analysis_at": None,
        },
        current_json=None,
        current_json_raw=None,
        generation_files={},
        executions=[
            ExecutionInfo(name="e1", succeeded=True, completed_at=now - timedelta(minutes=1)),
        ],
    )
    report = verify_runtime(snap, now=now)
    outcome = [c for c in report.checks if c.name == "analysis.outcome"]
    assert outcome
    assert outcome[0].level.value == "WARN"
    assert "not analysis success" in outcome[0].message.lower() or "RUNNING" in outcome[0].message


def test_analysis_outcome_ok_with_current_json_passes() -> None:
    report = verify_runtime(_base_snapshot(), now=_now())
    outcome = [c for c in report.checks if c.name == "analysis.outcome"]
    assert outcome
    assert outcome[0].level.value == "PASS"


def test_analysis_outcome_degraded_with_current_json_passes() -> None:
    now = _now()
    snap = _base_snapshot(
        analysis_status={
            "status": "DEGRADED",
            "run_id": "run-current",
            "generated_at": (now - timedelta(minutes=6)).isoformat(),
            "git_sha": "abc123",
            "last_successful_analysis_at": (now - timedelta(minutes=6)).isoformat(),
        }
    )
    report = verify_runtime(snap, now=now)
    outcome = [c for c in report.checks if c.name == "analysis.outcome"]
    assert outcome
    assert outcome[0].level.value == "PASS"


def test_analysis_error_fails() -> None:
    snap = _base_snapshot(analysis_status={"status": "ERROR", "git_sha": "abc123"})
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_current_json_missing_generation_fails() -> None:
    snap = _base_snapshot(generation_files={})
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_markets_coverage_over_100_fails() -> None:
    now = _now()
    bad_market = {
        "market_id": 1,
        "symbol": "ETH",
        "score": 50,
        "data_coverage_pct": 150.0,
        "observation_coverage_pct": 150.0,
        "usable_quote_coverage_pct": 90.0,
        "spread_coverage_pct": 88.0,
    }
    snap = _base_snapshot(
        generation_files={
            "lighter-mm/public/generations/gen1/markets.json": json.dumps({"markets": [bad_market]}),
            "lighter-mm/public/generations/gen1/latest.json": json.dumps({"markets": [bad_market], "generated_at": now.isoformat()}),
        }
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 1


def test_nan_infinity_fails() -> None:
    now = _now()
    bad = {
        "market_id": 1,
        "symbol": "ETH",
        "score": 50,
        "data_coverage_pct": "NaN",
        "observation_coverage_pct": 95.0,
        "usable_quote_coverage_pct": 90.0,
        "spread_coverage_pct": 88.0,
    }
    snap = _base_snapshot(
        generation_files={
            "lighter-mm/public/generations/gen1/markets.json": json.dumps({"markets": [bad]}),
            "lighter-mm/public/generations/gen1/latest.json": json.dumps({"markets": [bad], "generated_at": now.isoformat()}),
        }
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 1


def test_collector_run_mismatch_previous_analysis_warn() -> None:
    now = _now()
    snap = _base_snapshot(
        collector_status={**(_base_snapshot().collector_status or {}), "run_id": "new-run"},
        analysis_status={"status": "OK", "git_sha": "abc123", "run_id": "old-run", "generated_at": now.isoformat()},
        executions=[],
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 0
    assert any(c.name == "run_id.consistency" and c.level.value == "WARN" for c in report.checks)


def test_vercel_http_failure_fails() -> None:
    snap = _base_snapshot(dashboard_http_status=503, dashboard_body="")
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1


def test_json_output_valid() -> None:
    report = verify_runtime(_base_snapshot(), now=_now())
    data = json.loads(render_json(report))
    assert data["status"] == "healthy"
    assert "checks" in data


def test_warn_only_exit_0() -> None:
    now = _now()
    snap = _base_snapshot(
        analysis_status={
            "status": "OK",
            "git_sha": "oldsha",
            "generated_at": now.isoformat(),
        },
        executions=[],
    )
    report = verify_runtime(snap, now=now)
    assert report.exit_code() == 0
    assert report.status == "degraded"


def test_fail_exit_1() -> None:
    snap = _base_snapshot(worker_pool=WorkerPoolInfo(exists=False))
    report = verify_runtime(snap, now=_now())
    assert report.exit_code() == 1
