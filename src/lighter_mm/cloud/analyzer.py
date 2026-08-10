"""Cloud Run Job analyzer: read GCS-mounted Parquet, publish dashboard JSON."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from lighter_mm.analytics.aggregation import _rss_mb, analyze_range
from lighter_mm.cloud.analysis_outcome import is_stale_running, running_reference_timestamp
from lighter_mm.cloud.analyzer_publish import (
    _last_successful_analysis_at,
    _publish_analysis_status,
    _publish_dashboard,
)
from lighter_mm.cloud.analyzer_requests import (
    _claim_final_request,
    _finalize_final_request_failure,
    _finalize_final_request_success,
    select_run_to_analyze,
)
from lighter_mm.cloud.analyzer_target import (
    _analysis_sources_for_run,
    _analysis_window_ms,
    _durable_watermark_ms,
    _iso_to_ms,
)
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings, build_storage_backend
from lighter_mm.storage.lock import LeaderLock, LostLeadershipError
from lighter_mm.storage.state import RunState, now_iso

log = logging.getLogger(__name__)

# Backward-compatible re-exports for tests and callers.
__all__ = [
    "run_cloud_analyze",
    "select_run_to_analyze",
    "_analysis_window_ms",
    "_publish_dashboard",
    "_iso_to_ms",
    "_durable_watermark_ms",
]


def _log_lock_busy_diagnostics(
    *,
    run_id: str,
    existing: dict[str, Any] | None,
    lock_payload: dict[str, Any] | None,
    stale_minutes: float,
) -> bool:
    """Log lock-busy context. Returns whether existing status is stale RUNNING."""
    status = (existing or {}).get("status")
    started_at = (existing or {}).get("started_at")
    generated_at = (existing or {}).get("generated_at")
    heartbeat_at = (existing or {}).get("heartbeat_at")
    ref_ts = running_reference_timestamp(existing)
    stale = is_stale_running(existing, stale_minutes=stale_minutes)
    holder = (lock_payload or {}).get("holder_id")
    expires_at = (lock_payload or {}).get("expires_at")
    lock_run_id = (lock_payload or {}).get("run_id")
    log.warning(
        "analyzer_lock_busy run_id=%s existing_status=%s started_at=%s "
        "generated_at=%s heartbeat_at=%s ref_ts=%s lock_holder=%s lock_run_id=%s "
        "lock_expires_at=%s stale_running=%s stale_minutes=%s",
        run_id,
        status,
        started_at,
        generated_at,
        heartbeat_at,
        ref_ts,
        holder,
        lock_run_id,
        expires_at,
        stale,
        stale_minutes,
    )
    return stale


def _load_run_state(backend, sync: DurableSync) -> RunState | None:
    raw = backend.download_json(sync.state_key())
    if not raw:
        return None
    return RunState.model_validate(raw)


def _check_leadership(lost_lock: threading.Event, *, phase: str) -> None:
    if lost_lock.is_set():
        raise LostLeadershipError(f"analyzer lock lost before {phase}")


def run_cloud_analyze(settings: Settings) -> int:
    """Entry point for ``lighter-mm cloud-analyze`` Cloud Run Job."""
    backend = build_storage_backend(settings)
    sync = DurableSync(
        backend,
        run_id="public",
        gcs_prefix=settings.gcs_prefix,
        public_prefix=settings.gcs_public_prefix,
    )
    last_ok = _last_successful_analysis_at(backend, sync)
    prior_status = backend.download_json(sync.public_key("analysis_status.json"))

    selection = select_run_to_analyze(backend, settings)
    if selection is None:
        if prior_status and prior_status.get("status") == "RUNNING":
            log.info("no run to analyze while analysis RUNNING; leaving status unchanged")
            return 0
        status = "NOT_STARTED" if prior_status is None else "NO_ACTIVE_RUN"
        _publish_analysis_status(
            backend,
            sync,
            status=status,
            run_id=None,
            generated_at=now_iso(),
            last_successful_analysis_at=last_ok,
            git_sha=settings.git_sha,
            analyzer_version=settings.analyzer_version,
        )
        log.info("no run to analyze; published status=%s", status)
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
        existing = backend.download_json(sync.public_key("analysis_status.json"))
        lock_payload = backend.download_json(sync.analyzer_lock_key())
        stale = _log_lock_busy_diagnostics(
            run_id=run_id,
            existing=existing,
            lock_payload=lock_payload,
            stale_minutes=settings.analysis_stale_minutes,
        )
        if existing and existing.get("status") == "RUNNING":
            # Active lease/lock is normal exclusivity — do not force-unlock or
            # start a second analyzer. Stale RUNNING after OOM recovers when the
            # lease expires and the next execution acquires via CAS.
            if stale:
                log.warning(
                    "stale RUNNING with analyzer lock busy; not starting dual analysis; "
                    "wait for lease expiry (lease_seconds=%s) then re-run",
                    settings.analyzer_lock_lease_seconds,
                )
            else:
                log.info("analysis already running; leaving RUNNING status unchanged")
            return 0
        log.info("analysis lock busy; exiting without overwriting status")
        return 0

    lost_lock = threading.Event()
    stop = threading.Event()
    background_stopped = False
    execution_start_ms = int(time.time() * 1000)
    execution_start = time.time()
    started_at = now_iso()
    generated_at = started_at
    last_ok = _last_successful_analysis_at(backend, sync)
    final_claimed = False
    last_parquet_health: dict[str, Any] = {}

    def _stop_background(*, join_timeout: float = 5.0) -> None:
        """Stop renew/heartbeat before any final status publish (no RUNNING overwrite)."""
        nonlocal background_stopped
        if background_stopped:
            return
        stop.set()
        renew_thread.join(timeout=join_timeout)
        background_stopped = True

    def _renew_and_heartbeat_loop() -> None:
        while not stop.wait(settings.analyzer_lock_renew_interval_seconds):
            if not lock.renew(run_id, git_sha=settings.git_sha):
                log.error("lost analyzer lock during analysis")
                lost_lock.set()
                return
            if stop.is_set():
                return
            try:
                beat_at = now_iso()
                _publish_analysis_status(
                    backend,
                    sync,
                    status="RUNNING",
                    run_id=run_id,
                    generated_at=beat_at,
                    started_at=started_at,
                    heartbeat_at=beat_at,
                    last_successful_analysis_at=last_ok,
                    git_sha=settings.git_sha,
                    analyzer_version=settings.analyzer_version,
                )
            except Exception as exc:  # noqa: BLE001
                # Heartbeat write failure must not abort analysis; lock renew is separate.
                log.warning("analyzer heartbeat publish failed: %s", exc)

    renew_thread = threading.Thread(
        target=_renew_and_heartbeat_loop,
        name="analyzer-lock-renew-heartbeat",
        daemon=True,
    )
    renew_thread.start()

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
            heartbeat_at=started_at,
            last_successful_analysis_at=last_ok,
            git_sha=settings.git_sha,
            analyzer_version=settings.analyzer_version,
        )
        log.info(
            "analysis_started run_id=%s type=%s last_successful_analysis_at=%s rss_mb=%.1f",
            run_id,
            request_type,
            last_ok,
            _rss_mb(),
        )

        _check_leadership(lost_lock, phase="analysis")
        state = _load_run_state(backend, sync)
        if state is None:
            raise RuntimeError(f"run state missing for {run_id}")

        sources = _analysis_sources_for_run(settings, run_id)

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
            state, execution_start_ms=execution_start_ms, sources=sources
        )

        log.info(
            "analyzing run_id=%s type=%s start_ms=%s end_ms=%s durable_ms=%s books=%s rss_mb=%.1f",
            run_id,
            request_type,
            start_ms,
            end_ms,
            durable_ms,
            sources.books,
            _rss_mb(),
        )

        result = analyze_range(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            sources=sources,
            market_lifecycle=state.market_lifecycle,
            duckdb_memory_limit=settings.duckdb_memory_limit,
            duckdb_threads=settings.duckdb_threads,
            read_only=True,
        )
        parquet_health = result.get("parquet_health") or {}
        last_parquet_health = parquet_health
        if result.get("error"):
            raise RuntimeError(str(result["error"]))

        _check_leadership(lost_lock, phase="dashboard publish")
        log.info(
            "analysis_dashboard_build_start run_id=%s rss_mb=%.1f",
            run_id,
            _rss_mb(),
        )
        payload = build_dashboard_payload(
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            state=state,
            sources=sources,
            analysis_result=result,
        )
        log.info(
            "analysis_dashboard_build_done run_id=%s rss_mb=%.1f",
            run_id,
            _rss_mb(),
        )
        analysis_id = uuid.uuid4().hex[:16]
        _publish_dashboard(backend, sync, payload, analysis_id=analysis_id)

        scored = result.get("scored") or []
        candidates = [s for s in scored if s.candidate]
        completed_at = now_iso()
        duration_seconds = time.time() - execution_start
        analysis_status = (
            "DEGRADED" if parquet_health.get("status") == "degraded" else "OK"
        )
        _check_leadership(lost_lock, phase="final status publish")
        # Stop heartbeat before final publish so RUNNING cannot overwrite OK/DEGRADED.
        _stop_background()
        _publish_analysis_status(
            backend,
            sync,
            status=analysis_status,
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
            valid_parquet_files=int(parquet_health.get("valid_parquet_files") or 0),
            corrupt_parquet_files=int(parquet_health.get("corrupt_parquet_files") or 0),
            skipped_files=parquet_health.get("skipped_files") or None,
            parquet_health_status=parquet_health.get("status"),
            git_sha=settings.git_sha,
            analyzer_version=settings.analyzer_version,
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
            "duration_seconds=%.1f analysis_id=%s status=%s valid_files=%s corrupt_files=%s",
            run_id,
            len(scored),
            len(candidates),
            int(result.get("book_row_count") or 0),
            duration_seconds,
            analysis_id,
            analysis_status,
            parquet_health.get("valid_parquet_files"),
            parquet_health.get("corrupt_parquet_files"),
        )
        return 0
    except LostLeadershipError as exc:
        log.error("analysis aborted: %s", exc)
        _stop_background()
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
        _stop_background()
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
                duration_seconds=duration_seconds,
                valid_parquet_files=int(last_parquet_health.get("valid_parquet_files") or 0),
                corrupt_parquet_files=int(last_parquet_health.get("corrupt_parquet_files") or 0),
                skipped_files=last_parquet_health.get("skipped_files") or None,
                parquet_health_status=last_parquet_health.get("status"),
                git_sha=settings.git_sha,
                analyzer_version=settings.analyzer_version,
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
        _stop_background()
        lock.release()
