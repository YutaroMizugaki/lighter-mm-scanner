"""Cloud Build pipeline structure and preflight/bootstrap separation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUDBUILD = ROOT / "cloudbuild.yaml"
AUDIT_LIB = ROOT / "scripts" / "gcp_audit_lib.sh"
DOCTOR = ROOT / "scripts" / "gcp_doctor.sh"


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
    preflight_text = preflight.read_text(encoding="utf-8")
    assert "gcp_audit_lib.sh" in preflight_text
    # Preflight must remain read-only — no services enable execution.
    assert not any(
        line.strip().startswith("gcloud services enable")
        for line in preflight_text.splitlines()
        if not line.lstrip().startswith("echo")
    )


def test_gcp_doctor_script_exists() -> None:
    assert DOCTOR.exists()
    assert AUDIT_LIB.exists()
    doctor_text = DOCTOR.read_text(encoding="utf-8")
    assert "gcp_audit_lib.sh" in doctor_text
    assert "--from-trigger" in doctor_text
    assert "SAFE TO RETRY CLOUD BUILD" in doctor_text


def test_gcp_audit_lib_covers_deploy_prerequisites() -> None:
    lib = AUDIT_LIB.read_text(encoding="utf-8")
    for api in (
        "run.googleapis.com",
        "cloudscheduler.googleapis.com",
        "artifactregistry.googleapis.com",
        "storage.googleapis.com",
    ):
        assert api in lib
    for role in (
        "roles/run.admin",
        "roles/iam.serviceAccountUser",
        "roles/artifactregistry.writer",
        "roles/cloudscheduler.admin",
    ):
        assert role in lib
    assert "audit_check_scheduler_deploy_prereqs" in lib
    assert "audit_check_cloud_build_iam" in lib


def test_cloudbuild_automap_substitutions_enabled() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    assert "automapSubstitutions: true" in text


def test_cloudbuild_preflight_maps_project_id_to_env() -> None:
    text = CLOUDBUILD.read_text(encoding="utf-8")
    preflight_block = text[text.index("id: preflight") : text.index("id: deploy\n")]
    assert "PROJECT_ID=$PROJECT_ID" in preflight_block
    assert "_WORKER_POOL=${_WORKER_POOL}" in preflight_block
    assert "_ANALYZER_JOB=${_ANALYZER_JOB}" in preflight_block


def test_preflight_script_validates_required_env_vars() -> None:
    preflight = (ROOT / "scripts" / "cloudbuild_preflight.sh").read_text(encoding="utf-8")
    for var in (
        "PROJECT_ID",
        "_REGION",
        "_AR_REPO",
        "_GCS_BUCKET",
        "_GCS_PUBLIC_BUCKET",
        "_SERVICE_ACCOUNT",
    ):
        assert f': "${{{var}:?' in preflight, f"missing validation for {var}"


def test_preflight_failure_shows_remediation_commands() -> None:
    preflight = (ROOT / "scripts" / "cloudbuild_preflight.sh").read_text(encoding="utf-8")
    lib = AUDIT_LIB.read_text(encoding="utf-8")
    assert "audit_print_failure_help" in preflight
    assert "bootstrap_gcp.sh" in lib
    assert "gcloud projects add-iam-policy-binding" in lib
