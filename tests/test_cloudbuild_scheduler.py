"""Static assertions for Cloud Scheduler / Analyzer deploy in cloudbuild.yaml."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUDBUILD = ROOT / "cloudbuild.yaml"


def test_cloudbuild_scheduler_uses_run_jobs_v2_endpoint() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert "run.googleapis.com/v2/projects/" in text
    assert "jobs/${_ANALYZER_JOB}:run" in text
    assert "namespaces/" not in text
    assert "v1/namespaces" not in text


def test_cloudbuild_scheduler_iam_and_oauth() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert "roles/run.invoker" in text
    assert "oauth-service-account-email" in text
    assert "_SCHEDULER_SERVICE_ACCOUNT" in text
    bootstrap = ROOT / "scripts" / "bootstrap_gcp.sh"
    assert bootstrap.exists()
    assert "cloudscheduler.googleapis.com" in bootstrap.read_text(encoding="utf-8")


def test_cloudbuild_scheduler_schedule_and_name() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert '_ANALYZER_SCHEDULE: "*/30 * * * *"' in text
    assert "*/15 * * * *" not in text
    assert "lighter-mm-analyzer-schedule" in text


def test_cloudbuild_deploy_order_analyzer_before_scheduler() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    analyzer_pos = text.index("id: deploy-analyzer")
    grant_pos = text.index("id: grant-scheduler-invoker")
    scheduler_pos = text.index("id: deploy-analyzer-scheduler")
    assert analyzer_pos < grant_pos < scheduler_pos


def test_cloudbuild_analyzer_memory_is_8gi() -> None:
    """Analyzer container memory must be 8Gi; DuckDB limit stays 1GiB.

    Evidence: 4Gi OOMed after book_load (~2.7M rows / ~1.2Gi RSS) during
    book_aggregate over ~22h of hive Parquet via GCS FUSE.
    """
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert '_ANALYZER_MEMORY: 8Gi' in text
    assert '_ANALYZER_MEMORY: 4Gi' not in text
    assert '_ANALYZER_CPU: "2"' in text
    assert "DUCKDB_MEMORY_LIMIT=1GiB" in text
    assert "DUCKDB_THREADS=2" in text
    # Collector memory substitution must remain distinct / unchanged.
    assert "_MEMORY: 1Gi" in text


def test_cloudbuild_analyzer_task_timeout_is_3600() -> None:
    """Full-window analysis needs >600s (parquet materialize alone ~7min)."""
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert '_ANALYZER_TASK_TIMEOUT: "3600"' in text
    assert '--task-timeout="${_ANALYZER_TASK_TIMEOUT}"' in text
