"""Final analysis request selection, claim, and finalization."""

from __future__ import annotations

import logging
import time
from typing import Any

from lighter_mm.cloud.analyzer_target import _iso_to_ms
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings
from lighter_mm.storage.state import now_iso

log = logging.getLogger(__name__)

_FINAL_RUNNING_STALE_SECONDS = 3600


def _is_retryable_final_request(raw: dict[str, Any], *, max_attempts: int) -> bool:
    status = str(raw.get("status") or "")
    attempts = int(raw.get("attempts") or 0)
    if status == "done" or status == "failed":
        return False
    if status == "pending":
        return attempts < max_attempts
    if status == "running":
        last = raw.get("last_attempt_at")
        if attempts >= max_attempts:
            return False
        if not last:
            return True
        last_ms = _iso_to_ms(str(last))
        if last_ms is None:
            return True
        age_s = (time.time() * 1000 - last_ms) / 1000.0
        return age_s >= _FINAL_RUNNING_STALE_SECONDS
    return False


def _list_retryable_final_requests(
    backend, gcs_prefix: str, *, max_attempts: int
) -> list[dict[str, Any]]:
    prefix = f"{gcs_prefix.rstrip('/')}/analysis-requests/"
    pending: list[dict[str, Any]] = []
    for key in backend.list_keys(prefix):
        if not key.endswith(".json"):
            continue
        raw = backend.download_json(key)
        if raw and _is_retryable_final_request(raw, max_attempts=max_attempts):
            pending.append(raw)
    pending.sort(key=lambda r: r.get("requested_at") or "")
    return pending


def select_run_to_analyze(backend, settings: Settings) -> tuple[str, str] | None:
    """Return (run_id, request_type) — retryable pending final requests take priority."""
    pending = _list_retryable_final_requests(
        backend, settings.gcs_prefix, max_attempts=settings.max_final_analysis_attempts
    )
    if pending:
        run_id = str(pending[0]["run_id"])
        return run_id, "final"

    pointer = backend.download_json(
        f"{settings.gcs_prefix.rstrip('/')}/state/active_run.json"
    )
    if pointer and pointer.get("run_id"):
        return str(pointer["run_id"]), "incremental"
    return None


def _claim_final_request(
    backend,
    sync: DurableSync,
    run_id: str,
    *,
    max_attempts: int,
) -> bool:
    key = sync.analysis_request_key(run_id)
    raw = backend.download_json(key)
    if not raw or not _is_retryable_final_request(raw, max_attempts=max_attempts):
        return False
    attempts = int(raw.get("attempts") or 0) + 1
    if attempts > max_attempts:
        raw["status"] = "failed"
        raw["last_error"] = "max attempts exceeded"
        backend.upload_json(key, raw)
        return False
    now = now_iso()
    backend.upload_json(
        key,
        {
            **raw,
            "run_id": run_id,
            "type": "final",
            "status": "running",
            "attempts": attempts,
            "last_attempt_at": now,
        },
    )
    return True


def _finalize_final_request_success(
    backend,
    sync: DurableSync,
    run_id: str,
    *,
    completed_at: str,
    start_ms: int,
    end_ms: int,
    git_sha: str | None,
) -> None:
    marker = {
        "status": "completed",
        "run_id": run_id,
        "analysis_at": completed_at,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "git_sha": git_sha,
    }
    backend.upload_json(sync.final_analysis_marker_key(), marker)
    req_key = sync.analysis_request_key(run_id)
    backend.upload_json(
        req_key,
        {
            "run_id": run_id,
            "type": "final",
            "status": "done",
            "requested_at": completed_at,
            "completed_at": completed_at,
            "attempts": None,
            "last_attempt_at": completed_at,
            "last_error": None,
        },
    )


def _finalize_final_request_failure(
    backend,
    sync: DurableSync,
    run_id: str,
    *,
    error: str,
    max_attempts: int,
) -> None:
    key = sync.analysis_request_key(run_id)
    raw = backend.download_json(key) or {"run_id": run_id, "type": "final", "attempts": 0}
    attempts = int(raw.get("attempts") or 0)
    now = now_iso()
    if attempts >= max_attempts:
        raw.update(
            {
                "status": "failed",
                "last_error": error,
                "last_attempt_at": now,
                "attempts": attempts,
            }
        )
    else:
        raw.update(
            {
                "status": "pending",
                "last_error": error,
                "last_attempt_at": now,
                "attempts": attempts,
            }
        )
    backend.upload_json(key, raw)
