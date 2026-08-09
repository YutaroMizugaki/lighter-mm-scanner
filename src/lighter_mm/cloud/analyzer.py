"""Cloud Run Job analyzer: read GCS-mounted Parquet, publish dashboard JSON."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from lighter_mm.analytics.aggregation import AnalysisSources, analyze_range
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings, build_storage_backend
from lighter_mm.storage.lock import LeaderLock, LostLeadershipError
from lighter_mm.storage.state import RunState, now_iso

log = logging.getLogger(__name__)

_FINAL_RUNNING_STALE_SECONDS = 3600


def _iso_to_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _analysis_sources_for_run(settings: Settings, run_id: str) -> AnalysisSources:
    """Point DuckDB at GCS-mounted durable Parquet (read-only)."""
    mount = settings.analyzer_mount_path
    prefix = settings.gcs_prefix.rstrip("/")
    base = mount / prefix / "runs" / run_id
    return AnalysisSources(
        books=base / "books",
        trades=base / "trades",
        markouts=base / "markouts",
    )


def _durable_watermark_ms(state: RunState) -> int | None:
    return _iso_to_ms(state.last_successful_flush)


def _analysis_window_ms(
    state: RunState,
    *,
    execution_start_ms: int,
) -> tuple[int, int, int, int]:
    """Return (start_ms, end_ms, analysis_end_ms, durable_watermark_ms)."""
    start_ms = _iso_to_ms(state.started_at) or execution_start_ms
    durable_ms = _durable_watermark_ms(state)
    if durable_ms is None:
        raise RuntimeError(
            "missing last_successful_flush; refusing to analyze beyond durable GCS data"
        )

    if state.status == "completed" and state.ended_at:
        ended_ms = _iso_to_ms(state.ended_at) or durable_ms
        end_ms = min(ended_ms, durable_ms)
    else:
        end_ms = min(execution_start_ms, durable_ms)

    return start_ms, end_ms, end_ms, durable_ms


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


def _load_run_state(backend, sync: DurableSync) -> RunState | None:
    raw = backend.download_json(sync.state_key())
    if not raw:
        return None
    return RunState.model_validate(raw)


def _last_successful_analysis_at(backend, sync: DurableSync) -> str | None:
    status = backend.download_json(sync.public_key("analysis_status.json"))
    if status and status.get("status") == "OK":
        return status.get("last_successful_analysis_at") or status.get("generated_at")
    if status and status.get("last_successful_analysis_at"):
        return status.get("last_successful_analysis_at")
    marker = backend.download_json(sync.final_analysis_marker_key())
    if marker:
        return marker.get("analysis_at")
    return None


def _current_generation(backend, sync: DurableSync) -> dict[str, Any] | None:
    return backend.download_json(sync.public_key("current.json"))


def _publish_analysis_status(
    backend,
    sync: DurableSync,
    *,
    status: str,
    run_id: str,
    generated_at: str,
    error: str | None = None,
    last_successful_analysis_at: str | None = None,
    started_at: str | None = None,
    duration_seconds: float | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    analysis_end_ms: int | None = None,
    durable_watermark_ms: int | None = None,
    book_rows: int = 0,
    trade_rows: int = 0,
    markout_rows: int = 0,
    markets_analyzed: int = 0,
    candidates: int = 0,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "generated_at": generated_at,
    }
    if started_at:
        payload["started_at"] = started_at
    if status == "OK":
        payload.update(
            {
                "last_successful_analysis_at": last_successful_analysis_at or generated_at,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "analysis_end_ms": analysis_end_ms if analysis_end_ms is not None else end_ms,
                "durable_watermark_ms": durable_watermark_ms,
                "book_rows": book_rows,
                "trade_rows": trade_rows,
                "markout_rows": markout_rows,
                "markets_analyzed": markets_analyzed,
                "candidates": candidates,
            }
        )
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
    elif status == "RUNNING":
        pass
    else:
        payload["error"] = error
        if last_successful_analysis_at:
            payload["last_successful_analysis_at"] = last_successful_analysis_at
    backend.upload_json(sync.public_key("analysis_status.json"), payload, public=True)


def _generation_prefix(sync: DurableSync, analysis_id: str) -> str:
    return f"{sync.public_prefix}/generations/{analysis_id}"


def _publish_dashboard(
    backend,
    sync: DurableSync,
    payload: dict[str, Any],
    *,
    analysis_id: str,
) -> None:
    """Publish a generation-consistent dashboard bundle, then advance current.json."""
    gen_prefix = _generation_prefix(sync, analysis_id)
    generated_at = payload["latest"]["generated_at"]

    backend.upload_json(f"{gen_prefix}/markets.json", {
        "markets": payload["markets"],
        "generated_at": generated_at,
    }, public=True)
    backend.upload_json(f"{gen_prefix}/candidates.json", {
        "candidates": payload["candidates"],
    }, public=True)
    for sym, detail in payload["market_details"].items():
        backend.upload_json(
            f"{gen_prefix}/market/{sym}.json", detail, public=True
        )
    # latest.json is the commit marker for this generation.
    backend.upload_json(f"{gen_prefix}/latest.json", payload["latest"], public=True)

    backend.upload_json(
        sync.public_key("current.json"),
        {"analysis_id": analysis_id, "generated_at": generated_at},
        public=True,
    )

    # Best-effort legacy stable URLs for older dashboard deployments.
    try:
        backend.upload_json(sync.public_key("latest.json"), payload["latest"], public=True)
        backend.upload_json(
            sync.public_key("markets.json"),
            {"markets": payload["markets"], "generated_at": generated_at},
            public=True,
        )
        backend.upload_json(
            sync.public_key("candidates.json"),
            {"candidates": payload["candidates"]},
            public=True,
        )
        for sym, detail in payload["market_details"].items():
            backend.upload_json(sync.public_key(f"market/{sym}.json"), detail, public=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("legacy dashboard mirror failed: %s", exc)


def _check_leadership(lost_lock: threading.Event, *, phase: str) -> None:
    if lost_lock.is_set():
        raise LostLeadershipError(f"analyzer lock lost before {phase}")


def run_cloud_analyze(settings: Settings) -> int:
    """Entry point for ``lighter-mm cloud-analyze`` Cloud Run Job."""
    backend = build_storage_backend(settings)
    selection = select_run_to_analyze(backend, settings)
    if selection is None:
        log.info("no run to analyze; exiting")
        return 0

    run_id, request_type = selection
    sync = DurableSync(
        backend,
        run_id=run_id,
        gcs_prefix=settings.gcs_prefix,
        public_prefix=settings.gcs_public_prefix,
    )
    holder_id = uuid.uuid4().hex
    lock = LeaderLock(
        backend,
        sync.analyzer_lock_key(),
        holder_id=holder_id,
        lease_seconds=settings.analyzer_lock_lease_seconds,
    )
    if not lock.acquire(run_id, git_sha=settings.git_sha):
        log.info("analysis already running; exiting")
        return 0

    lost_lock = threading.Event()
    stop = threading.Event()

    def _renew_loop() -> None:
        while not stop.wait(settings.analyzer_lock_renew_interval_seconds):
            if not lock.renew(run_id, git_sha=settings.git_sha):
                log.error("lost analyzer lock during analysis")
                lost_lock.set()
                return

    renew_thread = threading.Thread(target=_renew_loop, name="analyzer-lock-renew", daemon=True)
    renew_thread.start()

    execution_start_ms = int(time.time() * 1000)
    execution_start = time.time()
    started_at = now_iso()
    generated_at = started_at
    last_ok = _last_successful_analysis_at(backend, sync)
    final_claimed = False

    try:
        if request_type == "final":
            if not _claim_final_request(
                backend,
                sync,
                run_id,
                max_attempts=settings.max_final_analysis_attempts,
            ):
                log.info("final request for %s not claimable; skipping", run_id)
                return 0
            final_claimed = True

        _check_leadership(lost_lock, phase="analysis status publish")
        _publish_analysis_status(
            backend,
            sync,
            status="RUNNING",
            run_id=run_id,
            generated_at=generated_at,
            started_at=started_at,
        )
        log.info(
            "analysis_started run_id=%s type=%s last_successful_analysis_at=%s",
            run_id,
            request_type,
            last_ok,
        )

        _check_leadership(lost_lock, phase="analysis")
        state = _load_run_state(backend, sync)
        if state is None:
            raise RuntimeError(f"run state missing for {run_id}")

        if request_type == "final":
            if state.status != "completed" or not state.ended_at:
                log.warning(
                    "final request for %s but state is not completed (status=%s); deferring",
                    run_id,
                    state.status,
                )
                if final_claimed:
                    _finalize_final_request_failure(
                        backend,
                        sync,
                        run_id,
                        error="run state not completed",
                        max_attempts=settings.max_final_analysis_attempts,
                    )
                return 0

        start_ms, end_ms, analysis_end_ms, durable_ms = _analysis_window_ms(
            state, execution_start_ms=execution_start_ms
        )

        sources = _analysis_sources_for_run(settings, run_id)
        log.info(
            "analyzing run_id=%s type=%s start_ms=%s end_ms=%s durable_ms=%s books=%s",
            run_id,
            request_type,
            start_ms,
            end_ms,
            durable_ms,
            sources.books,
        )

        result = analyze_range(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            sources=sources,
            duckdb_memory_limit=settings.duckdb_memory_limit,
            duckdb_threads=settings.duckdb_threads,
        )
        if result.get("error"):
            raise RuntimeError(str(result["error"]))

        _check_leadership(lost_lock, phase="dashboard publish")
        payload = build_dashboard_payload(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            state=state,
            sources=sources,
            analysis_result=result,
        )
        analysis_id = uuid.uuid4().hex[:16]
        _publish_dashboard(backend, sync, payload, analysis_id=analysis_id)

        scored = result.get("scored") or []
        candidates = [s for s in scored if s.candidate]
        completed_at = now_iso()
        duration_seconds = time.time() - execution_start
        _check_leadership(lost_lock, phase="final status publish")
        _publish_analysis_status(
            backend,
            sync,
            status="OK",
            run_id=run_id,
            generated_at=completed_at,
            last_successful_analysis_at=completed_at,
            started_at=started_at,
            duration_seconds=duration_seconds,
            start_ms=start_ms,
            end_ms=end_ms,
            analysis_end_ms=analysis_end_ms,
            durable_watermark_ms=durable_ms,
            book_rows=int(result.get("book_row_count") or 0),
            trade_rows=int(result.get("trade_row_count") or 0),
            markout_rows=int(result.get("markout_row_count") or 0),
            markets_analyzed=len(scored),
            candidates=len(candidates),
        )

        if request_type == "final":
            _check_leadership(lost_lock, phase="final marker")
            _finalize_final_request_success(
                backend,
                sync,
                run_id,
                completed_at=completed_at,
                start_ms=start_ms,
                end_ms=end_ms,
                git_sha=settings.git_sha,
            )

        log.info(
            "analysis_completed run_id=%s markets=%s candidates=%s book_rows=%s "
            "duration_seconds=%.1f analysis_id=%s",
            run_id,
            len(scored),
            len(candidates),
            int(result.get("book_row_count") or 0),
            duration_seconds,
            analysis_id,
        )
        return 0
    except LostLeadershipError as exc:
        log.error("analysis aborted: %s", exc)
        if request_type == "final" and final_claimed:
            _finalize_final_request_failure(
                backend,
                sync,
                run_id,
                error=str(exc),
                max_attempts=settings.max_final_analysis_attempts,
            )
        return 1
    except Exception as exc:  # noqa: BLE001
        failed_at = now_iso()
        duration_seconds = time.time() - execution_start
        log.exception(
            "analysis_failed run_id=%s error=%s duration_seconds=%.1f",
            run_id,
            exc,
            duration_seconds,
        )
        if not lost_lock.is_set():
            _publish_analysis_status(
                backend,
                sync,
                status="ERROR",
                run_id=run_id,
                generated_at=failed_at,
                started_at=started_at,
                error=str(exc),
                last_successful_analysis_at=last_ok,
            )
        if request_type == "final" and final_claimed:
            _finalize_final_request_failure(
                backend,
                sync,
                run_id,
                error=str(exc),
                max_attempts=settings.max_final_analysis_attempts,
            )
        return 1
    finally:
        stop.set()
        renew_thread.join(timeout=5.0)
        lock.release()
