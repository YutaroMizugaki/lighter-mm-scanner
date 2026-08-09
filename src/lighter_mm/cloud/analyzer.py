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
from lighter_mm.storage.lock import LeaderLock
from lighter_mm.storage.state import RunState, now_iso

log = logging.getLogger(__name__)


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


def _list_pending_final_requests(backend, gcs_prefix: str) -> list[dict[str, Any]]:
    prefix = f"{gcs_prefix.rstrip('/')}/analysis-requests/"
    pending: list[dict[str, Any]] = []
    for key in backend.list_keys(prefix):
        if not key.endswith(".json"):
            continue
        raw = backend.download_json(key)
        if raw and raw.get("status") == "pending":
            pending.append(raw)
    pending.sort(key=lambda r: r.get("requested_at") or "")
    return pending


def select_run_to_analyze(backend, settings: Settings) -> tuple[str, str] | None:
    """Return (run_id, request_type) — pending final requests take priority."""
    pending = _list_pending_final_requests(backend, settings.gcs_prefix)
    if pending:
        run_id = str(pending[0]["run_id"])
        return run_id, "final"

    pointer = backend.download_json(
        f"{settings.gcs_prefix.rstrip('/')}/state/active_run.json"
    )
    if pointer and pointer.get("run_id"):
        return str(pointer["run_id"]), "incremental"
    return None


def _analysis_window_ms(state: RunState, *, execution_start_ms: int) -> tuple[int, int]:
    start_ms = _iso_to_ms(state.started_at) or execution_start_ms
    if state.status == "completed" and state.ended_at:
        end_ms = _iso_to_ms(state.ended_at) or execution_start_ms
    else:
        end_ms = execution_start_ms
    return start_ms, end_ms


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


def _publish_dashboard(
    backend,
    sync: DurableSync,
    payload: dict[str, Any],
) -> None:
    backend.upload_json(sync.public_key("latest.json"), payload["latest"], public=True)
    backend.upload_json(
        sync.public_key("markets.json"),
        {
            "markets": payload["markets"],
            "generated_at": payload["latest"]["generated_at"],
        },
        public=True,
    )
    backend.upload_json(
        sync.public_key("candidates.json"),
        {"candidates": payload["candidates"]},
        public=True,
    )
    for sym, detail in payload["market_details"].items():
        backend.upload_json(
            sync.public_key(f"market/{sym}.json"), detail, public=True
        )


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

    stop = threading.Event()

    def _renew_loop() -> None:
        while not stop.wait(settings.analyzer_lock_renew_interval_seconds):
            if not lock.renew(run_id, git_sha=settings.git_sha):
                log.error("lost analyzer lock during analysis")
                return

    renew_thread = threading.Thread(target=_renew_loop, name="analyzer-lock-renew", daemon=True)
    renew_thread.start()

    execution_start_ms = int(time.time() * 1000)
    execution_start = time.time()
    started_at = now_iso()
    generated_at = started_at
    last_ok = _last_successful_analysis_at(backend, sync)

    try:
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

        state = _load_run_state(backend, sync)
        if state is None:
            raise RuntimeError(f"run state missing for {run_id}")

        sources = _analysis_sources_for_run(settings, run_id)
        start_ms, end_ms = _analysis_window_ms(state, execution_start_ms=execution_start_ms)

        log.info(
            "analyzing run_id=%s type=%s start_ms=%s end_ms=%s books=%s",
            run_id,
            request_type,
            start_ms,
            end_ms,
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

        payload = build_dashboard_payload(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            state=state,
            sources=sources,
            analysis_result=result,
        )
        _publish_dashboard(backend, sync, payload)

        scored = result.get("scored") or []
        candidates = [s for s in scored if s.candidate]
        completed_at = now_iso()
        duration_seconds = time.time() - execution_start
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
            book_rows=int(result.get("book_row_count") or 0),
            trade_rows=int(result.get("trade_row_count") or 0),
            markout_rows=int(result.get("markout_row_count") or 0),
            markets_analyzed=len(scored),
            candidates=len(candidates),
        )

        if request_type == "final":
            marker = {
                "status": "completed",
                "run_id": run_id,
                "analysis_at": generated_at,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "git_sha": settings.git_sha,
            }
            backend.upload_json(sync.final_analysis_marker_key(), marker)
            req_key = sync.analysis_request_key(run_id)
            backend.upload_json(
                req_key,
                {
                    "run_id": run_id,
                    "type": "final",
                    "status": "done",
                    "requested_at": generated_at,
                    "completed_at": generated_at,
                },
            )

        log.info(
            "analysis_completed run_id=%s markets=%s candidates=%s book_rows=%s "
            "duration_seconds=%.1f",
            run_id,
            len(scored),
            len(candidates),
            int(result.get("book_row_count") or 0),
            duration_seconds,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        failed_at = now_iso()
        duration_seconds = time.time() - execution_start
        log.exception(
            "analysis_failed run_id=%s error=%s duration_seconds=%.1f",
            run_id,
            exc,
            duration_seconds,
        )
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
        return 1
    finally:
        stop.set()
        renew_thread.join(timeout=5.0)
        lock.release()
