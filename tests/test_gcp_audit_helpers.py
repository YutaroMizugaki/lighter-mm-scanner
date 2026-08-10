"""Unit tests for GCP audit helpers and gcp_audit_lib.sh regressions."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_LIB = ROOT / "scripts" / "gcp_audit_lib.sh"
HELPERS = ROOT / "scripts" / "audit_gcp_helpers.py"

_spec = importlib.util.spec_from_file_location("audit_gcp_helpers", HELPERS)
assert _spec and _spec.loader
_helpers = importlib.util.module_from_spec(_spec)
sys.modules["audit_gcp_helpers"] = _helpers
_spec.loader.exec_module(_helpers)

deployment_provenance_ok = _helpers.deployment_provenance_ok
deployment_provenance_check = _helpers.deployment_provenance_check
digests_match = _helpers.digests_match
extract_container_image = _helpers.extract_container_image
extract_git_sha_env = _helpers.extract_git_sha_env
extract_trigger_service_account = _helpers.extract_trigger_service_account
image_digest = _helpers.image_digest
normalize_service_account_email = _helpers.normalize_service_account_email


def _run_bash(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_extract_image_from_worker_pool_spec_template() -> None:
    payload = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "image": "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:abc123",
                            "env": [{"name": "GIT_SHA", "value": "deadbeef"}],
                        }
                    ]
                }
            }
        }
    }
    assert (
        extract_container_image(payload)
        == "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:abc123"
    )
    assert extract_git_sha_env(payload) == "deadbeef"


def test_extract_image_from_job_template_template() -> None:
    payload = {
        "template": {
            "template": {
                "containers": [
                    {"image": "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:def456"}
                ]
            }
        }
    }
    assert extract_container_image(payload) == "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:def456"


def test_extract_image_from_template_containers() -> None:
    payload = {
        "template": {
            "containers": [{"image": "asia-northeast1-docker.pkg.dev/p/r/collector:commit1"}]
        }
    }
    assert extract_container_image(payload) == "asia-northeast1-docker.pkg.dev/p/r/collector:commit1"


def test_digest_extraction_and_match() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:abc123def"
    assert image_digest(image) == "sha256:abc123def"
    assert digests_match("sha256:abc123def", "abc123def")
    assert digests_match("abc123def", "sha256:abc123def")


def test_provenance_digest_match() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:abc123"
    assert deployment_provenance_ok(
        deployed_image=image,
        commit_sha="ignored",
        ar_digest="sha256:abc123",
    )


def test_provenance_tag_match() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector:commitsha1"
    assert deployment_provenance_ok(deployed_image=image, commit_sha="commitsha1")


def test_provenance_git_sha_only_fails_without_tag_or_digest() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:xyz"
    ok, reason = deployment_provenance_check(
        deployed_image=image,
        commit_sha="commitsha1",
        git_sha_env="commitsha1",
    )
    assert not ok
    assert reason == "no_match"


def test_provenance_wrong_digest_git_sha_only_fails() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:wrong"
    ok, reason = deployment_provenance_check(
        deployed_image=image,
        commit_sha="commitsha1",
        ar_digest="",
        git_sha_env="commitsha1",
    )
    assert not ok
    assert reason == "no_match"


def test_provenance_digest_mismatch_fails_even_when_git_sha_matches() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:deployed"
    ok, reason = deployment_provenance_check(
        deployed_image=image,
        commit_sha="commitsha1",
        ar_digest="sha256:expected",
        git_sha_env="commitsha1",
    )
    assert not ok
    assert reason == "digest_mismatch"


def test_audit_verify_deployment_digest_mismatch_fails_despite_git_sha() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:deployed"
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
audit_verify_deployment_image "worker pool" "{image}" "commitsha1" "sha256:expected" "commitsha1" || true
if audit_print_summary; then exit 1; else exit 0; fi
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "FAIL worker pool image digest does not match Artifact Registry" in out


def test_audit_verify_deployment_digest_bash() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:abc123"
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
audit_verify_deployment_image "worker pool" "{image}" "ignored" "sha256:abc123" "" || true
audit_print_summary
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "worker pool image digest matches Artifact Registry" in out


def test_audit_verify_deployment_empty_image_fails() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
audit_verify_deployment_image "worker pool" "" "abc123" "" "" || true
if audit_print_summary; then exit 1; else exit 0; fi
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "FAIL worker pool deployment image is empty" in out


def test_audit_api_enabled_permission_denied_unknown_not_fail() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
gcloud() {{
  if [[ "$1" == "services" && "$2" == "list" ]]; then
    echo "PERMISSION_DENIED: missing serviceusage.services.list" >&2
    return 1
  fi
  return 1
}}
export -f gcloud
audit_api_enabled "proj" "logging.googleapis.com" "Cloud Logging API" || true
if audit_print_summary; then exit 0; else exit 1; fi
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "UNKNOWN Cloud Logging API" in out
    assert "is not enabled" not in out


def test_audit_api_enabled_disabled_is_fail() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
gcloud() {{
  if [[ "$1" == "services" && "$2" == "list" ]]; then
    printf ''
    return 0
  fi
  return 1
}}
export -f gcloud
audit_api_enabled "proj" "logging.googleapis.com" "Cloud Logging API" || true
if audit_print_summary; then exit 1; else exit 0; fi
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "FAIL Cloud Logging API" in out
    assert "is not enabled" in out


def test_audit_cloud_build_executor_sa_prefers_build_id() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
BUILD_ID="build-123"
gcloud() {{
  if [[ "$1" == "builds" && "$2" == "describe" ]]; then
    echo "projects/my-project/serviceAccounts/build-executor@project.iam.gserviceaccount.com"
    return 0
  fi
  if [[ "$1" == "builds" && "$2" == "get-default-service-account" ]]; then
    echo "default-sa@developer.gserviceaccount.com"
    return 0
  fi
  return 1
}}
export -f gcloud
CLOUD_BUILD_TRIGGER_SERVICE_ACCOUNT="trigger-sa@project.iam.gserviceaccount.com"
result="$(audit_cloud_build_executor_sa "my-project" "asia-northeast1")"
printf '%s' "$result"
"""
    code, out = _run_bash(script)
    assert code == 0
    assert out.strip() == "build-executor@project.iam.gserviceaccount.com"


def test_audit_cloud_build_executor_sa_prefers_trigger_sa() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
gcloud() {{
  if [[ "$1" == "builds" && "$2" == "get-default-service-account" ]]; then
    echo "default-sa@developer.gserviceaccount.com"
    return 0
  fi
  return 1
}}
export -f gcloud
CLOUD_BUILD_TRIGGER_SERVICE_ACCOUNT="projects/my-project/serviceAccounts/trigger-sa@project.iam.gserviceaccount.com"
result="$(audit_cloud_build_executor_sa "my-project" "asia-northeast1")"
printf '%s' "$result"
"""
    code, out = _run_bash(script)
    assert code == 0
    assert out.strip() == "trigger-sa@project.iam.gserviceaccount.com"


def test_audit_cloud_build_executor_sa_uses_gcloud_default() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
gcloud() {{
  if [[ "$1" == "builds" && "$2" == "get-default-service-account" ]]; then
    echo "197246566540-compute@developer.gserviceaccount.com"
    return 0
  fi
  return 1
}}
export -f gcloud
result="$(audit_cloud_build_executor_sa "my-project" "asia-northeast1")"
printf '%s' "$result"
"""
    code, out = _run_bash(script)
    assert code == 0
    assert out.strip() == "197246566540-compute@developer.gserviceaccount.com"


def test_audit_api_enabled_iam_not_passed_by_unrelated_probe() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
gcloud() {{
  if [[ "$1" == "services" && "$2" == "list" ]]; then
    echo "PERMISSION_DENIED: missing serviceusage.services.list" >&2
    return 1
  fi
  if [[ "$1" == "logging" ]]; then
    return 0
  fi
  if [[ "$1" == "iam" && "$2" == "service-accounts" && "$3" == "list" ]]; then
    return 1
  fi
  return 1
}}
export -f gcloud
audit_api_enabled "proj" "iam.googleapis.com" "IAM API" || true
if audit_print_summary; then exit 0; else exit 1; fi
"""
    code, out = _run_bash(script)
    assert code == 0
    assert "UNKNOWN IAM API" in out
    assert "PASS IAM API" not in out
    assert "is not enabled" not in out


def test_extract_trigger_service_account() -> None:
    payload = {
        "name": "lighter-mm-main",
        "serviceAccount": "projects/my-project/serviceAccounts/trigger-sa@project.iam.gserviceaccount.com",
    }
    assert (
        extract_trigger_service_account(payload)
        == "trigger-sa@project.iam.gserviceaccount.com"
    )
    assert extract_trigger_service_account({"serviceAccountEmail": "legacy@x"}) == "legacy@x"
    assert extract_trigger_service_account({}) == ""


def test_normalize_service_account_email() -> None:
    resource = "projects/my-project/serviceAccounts/build-executor@project.iam.gserviceaccount.com"
    assert normalize_service_account_email(resource) == "build-executor@project.iam.gserviceaccount.com"
    assert normalize_service_account_email("plain@project.iam.gserviceaccount.com") == (
        "plain@project.iam.gserviceaccount.com"
    )


def test_helpers_cli_image_command() -> None:
    payload = {"template": {"containers": [{"image": "img@sha256:abc"}]}}
    proc = subprocess.run(
        ["python3", str(HELPERS), "image"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == "img@sha256:abc"
