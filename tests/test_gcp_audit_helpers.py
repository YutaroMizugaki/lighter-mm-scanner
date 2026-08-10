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
digests_match = _helpers.digests_match
extract_container_image = _helpers.extract_container_image
extract_git_sha_env = _helpers.extract_git_sha_env
image_digest = _helpers.image_digest


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


def test_provenance_git_sha_env_match() -> None:
    image = "asia-northeast1-docker.pkg.dev/p/r/collector@sha256:xyz"
    assert deployment_provenance_ok(
        deployed_image=image,
        commit_sha="commitsha1",
        git_sha_env="commitsha1",
    )


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


def test_audit_cloud_build_executor_sa_prefers_env() -> None:
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
CLOUD_BUILD_SERVICE_ACCOUNT="197246566540-compute@developer.gserviceaccount.com"
result="$(audit_cloud_build_executor_sa "my-project")"
printf '%s' "$result"
"""
    code, out = _run_bash(script)
    assert code == 0
    assert out.strip() == "197246566540-compute@developer.gserviceaccount.com"


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
