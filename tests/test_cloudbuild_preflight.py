"""Cloud Build pipeline structure and preflight/bootstrap separation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUDBUILD = ROOT / "cloudbuild.yaml"


def test_cloudbuild_has_no_enable_apis_step() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert "id: enable-apis" not in text
    assert "gcloud services enable" not in text


def test_cloudbuild_preflight_before_deploy() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    preflight_pos = text.index("id: preflight")
    deploy_pos = text.index("id: deploy\n")
    analyzer_pos = text.index("id: deploy-analyzer")
    assert preflight_pos < deploy_pos < analyzer_pos


def test_cloudbuild_deploy_dag() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert 'waitFor: ["ruff", "pytest"]' in text
    assert 'waitFor: ["preflight"]' in text
    grant_pos = text.index("id: grant-scheduler-invoker")
    scheduler_pos = text.index("id: deploy-analyzer-scheduler")
    assert grant_pos < scheduler_pos
    assert 'waitFor: ["deploy-analyzer"]' in text


def test_bootstrap_script_exists() -> None:
    bootstrap = ROOT / "scripts" / "bootstrap_gcp.sh"
    preflight = ROOT / "scripts" / "cloudbuild_preflight.sh"
    assert bootstrap.exists()
    assert preflight.exists()
    assert "gcloud services enable" in bootstrap.read_text(encoding="utf-8")
    assert "gcloud services enable" not in preflight.read_text(encoding="utf-8")
