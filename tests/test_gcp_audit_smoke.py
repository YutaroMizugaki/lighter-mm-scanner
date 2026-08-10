"""Post-deploy smoke shell helper behavior."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_LIB = ROOT / "scripts" / "gcp_audit_lib.sh"
AUDIT_STATUS_JSON = ROOT / "scripts" / "audit_status_json.py"


def _run_audit(
    *,
    commit_sha: str,
    collector_json: str | None = None,
    analysis_json: str | None = None,
    collector_wait: int = 0,
) -> tuple[int, str]:
    collector_body = json.dumps(collector_json) if collector_json is not None else ""
    analysis_body = json.dumps(analysis_json) if analysis_json is not None else ""
    script = f"""
set -euo pipefail
source "{AUDIT_LIB}"
audit_reset_counters
COMMIT_SHA="{commit_sha}"
audit_fetch_public_json() {{
  local bucket="$1"
  local object_path="$2"
  if [[ "$object_path" == *collector_status.json ]]; then
    printf '%s' '{collector_body.replace("'", "'\\''")}'
  elif [[ "$object_path" == *analysis_status.json ]]; then
    printf '%s' '{analysis_body.replace("'", "'\\''")}'
  else
    printf ''
  fi
}}
audit_check_public_collector_status "bucket" "prefix" "{commit_sha}" || true
if [[ "{collector_wait}" -gt 0 ]]; then
  :
fi
audit_check_public_analysis_status "bucket" "prefix" "{commit_sha}" || true
if audit_print_summary; then exit 0; else exit 1; fi
"""
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_parse_status_json_malformed() -> None:
    proc = subprocess.run(
        ["python3", str(AUDIT_STATUS_JSON)],
        input="{not-json",
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(proc.stdout)
    assert parsed["present"] is True
    assert parsed["valid"] is False


def test_collector_matching_sha_passes() -> None:
    code, out = _run_audit(
        commit_sha="abc123",
        collector_json={"git_sha": "abc123", "status": "OK"},
        analysis_json=None,
    )
    assert code == 0
    assert "collector_status.json git_sha matches abc123" in out


def test_collector_old_sha_fails() -> None:
    code, out = _run_audit(
        commit_sha="newsha",
        collector_json={"git_sha": "oldsha", "status": "OK"},
    )
    assert code != 0
    assert "FAIL collector_status.json git_sha mismatch" in out


def test_collector_malformed_json_fails() -> None:
    code, out = _run_audit(
        commit_sha="abc123",
        collector_json="{bad",
    )
    assert code != 0
    assert "FAIL collector_status.json malformed JSON" in out


def test_analyzer_old_sha_warns_build_passes() -> None:
    code, out = _run_audit(
        commit_sha="newsha",
        collector_json={"git_sha": "newsha", "status": "OK"},
        analysis_json={"git_sha": "oldsha", "status": "OK"},
    )
    assert code == 0
    assert "WARN analysis_status.json stale git_sha" in out
    assert "new analyzer revision has not executed yet" in out


def test_analyzer_missing_warns_build_passes() -> None:
    code, out = _run_audit(
        commit_sha="newsha",
        collector_json={"git_sha": "newsha", "status": "OK"},
        analysis_json=None,
    )
    assert code == 0
    assert "WARN analysis_status.json not yet available" in out


def test_analyzer_malformed_json_fails() -> None:
    code, out = _run_audit(
        commit_sha="newsha",
        collector_json={"git_sha": "newsha", "status": "OK"},
        analysis_json="{bad",
    )
    assert code != 0
    assert "FAIL analysis_status.json malformed" in out


def test_analyzer_matching_sha_passes() -> None:
    code, out = _run_audit(
        commit_sha="newsha",
        collector_json={"git_sha": "newsha", "status": "OK"},
        analysis_json={"git_sha": "newsha", "status": "OK"},
    )
    assert code == 0
    assert "analysis_status.json git_sha matches newsha" in out
