"""Collector durability payload builders (no storage I/O)."""

from __future__ import annotations

from typing import Any

from lighter_mm.storage.state import RunState, now_iso


def project_collector_counters(
    state: RunState,
    *,
    samples_written: int,
    trades_written: int,
    markouts_written: int,
    dropped_connections: int,
    book_resyncs: int,
    nonce_gaps: int,
    deployment_gaps: int,
    last_trade_timestamp_ms: int | None,
    bytes_uploaded: int,
    holder_id: str,
    git_sha: str | None,
) -> None:
    """Update RunState in-place from live collector counters."""
    state.samples_written = samples_written
    state.trades_written = trades_written
    state.markouts_written = markouts_written
    state.dropped_connections = dropped_connections
    state.book_resyncs = book_resyncs
    state.nonce_gaps = nonce_gaps
    state.deployment_gaps = deployment_gaps
    state.last_trade_timestamp_ms = last_trade_timestamp_ms
    state.bytes_uploaded = bytes_uploaded
    state.holder_id = holder_id
    state.git_sha = git_sha
    state.touch()


def build_active_run_pointer(
    *,
    run_id: str,
    status: str,
    git_sha: str | None,
    note: str | None = None,
    shutdown_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "updated_at": now_iso(),
        "git_sha": git_sha,
    }
    if note is not None:
        payload["note"] = note
    if shutdown_reason is not None:
        payload["shutdown_reason"] = shutdown_reason
    return payload


def build_final_analysis_request_payload(*, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "type": "final",
        "status": "pending",
        "requested_at": now_iso(),
        "attempts": 0,
        "last_attempt_at": None,
        "last_error": None,
    }
