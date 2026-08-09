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
    assert "*/15 * * * *" in text
    assert "lighter-mm-analyzer-schedule" in text


def test_cloudbuild_deploy_order_analyzer_before_scheduler() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    analyzer_pos = text.index("id: deploy-analyzer")
    grant_pos = text.index("id: grant-scheduler-invoker")
    scheduler_pos = text.index("id: deploy-analyzer-scheduler")
    assert analyzer_pos < grant_pos < scheduler_pos
