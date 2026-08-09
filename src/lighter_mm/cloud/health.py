"""Collector and WebSocket health helpers for dashboard payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from lighter_mm.storage.state import RunState


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def _age_minutes(ts: str | None, *, now: datetime | None = None) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    ref = now or datetime.now(UTC)
    return (ref - parsed).total_seconds() / 60.0


def collector_status_label(
    state: RunState | None,
    *,
    ok_minutes: float,
    warn_minutes: float,
    degraded: bool = False,
    startup_grace_minutes: float = 5.0,
    last_sync_attempt_at: str | None = None,
    consecutive_sync_failures: int = 0,
) -> str:
    """Collector health label (independent of analysis freshness)."""
    if state is None:
        return "ERROR"
    if state.status == "completed":
        return "COMPLETED"
    if state.status == "error":
        return "ERROR"

    flush = state.last_successful_flush
    started_age = _age_minutes(state.started_at)
    in_grace = started_age is not None and started_age <= startup_grace_minutes

    if not flush:
        if in_grace and state.status == "running":
            return "DEGRADED" if degraded else "COLLECTING"
        if state.status == "running":
            if consecutive_sync_failures > 0 or last_sync_attempt_at:
                return "DEGRADED"
            return "DEGRADED"
        return "ERROR"

    age_min = _age_minutes(flush)
    if age_min is None:
        return "ERROR"
    if degraded and state.status == "running" and age_min <= ok_minutes:
        return "DEGRADED"
    if state.status == "running" and age_min <= ok_minutes:
        return "COLLECTING"
    if age_min <= warn_minutes:
        return "STALE"
    return "OFFLINE" if state.status == "running" else state.status.upper()


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _ws_degraded(ws_runtime: dict[str, object] | None) -> list[str]:
    warnings: list[str] = []
    if not ws_runtime:
        return warnings
    connected = int(ws_runtime.get("connected_shards") or 0)
    total = int(ws_runtime.get("total_shards") or 0)
    planned = int(ws_runtime.get("planned_channels") or ws_runtime.get("subscribed_channels") or 0)
    acked = int(ws_runtime.get("acked_channels") or ws_runtime.get("subscribed_channels") or 0)
    if total > 0 and connected < total:
        warnings.append(f"WebSocket degraded: {connected}/{total} shards connected.")
    if planned > 0 and acked < planned:
        warnings.append(f"Subscription ACK incomplete: {acked}/{planned} channels acked.")
    return warnings


def _book_health_warnings(
    *,
    settings,
    last_book_sample_at_ms: int | None,
    last_book_row_at_ms: int | None,
    started_at: str | None,
    startup_grace_minutes: float,
) -> list[str]:
    warnings: list[str] = []
    started_age = _age_minutes(started_at)
    in_grace = started_age is not None and started_age <= startup_grace_minutes

    if not in_grace and last_book_row_at_ms is not None and last_book_sample_at_ms is None:
        warnings.append(
            "Book rows are being written but no usable book samples have been recorded."
        )

    if last_book_sample_at_ms is not None:
        age_s = (datetime.now(UTC).timestamp() * 1000 - last_book_sample_at_ms) / 1000.0
        stale_threshold_s = max(settings.book_sample_interval_seconds * 3, 30)
        if age_s > stale_threshold_s:
            warnings.append(
                f"Usable book samples stale: last sample {age_s:.0f}s ago "
                f"(threshold {stale_threshold_s:.0f}s)."
            )
    elif (
        not in_grace
        and last_book_row_at_ms is not None
        and (datetime.now(UTC).timestamp() * 1000 - last_book_row_at_ms) / 1000.0
        > max(settings.book_sample_interval_seconds * 3, 30)
    ):
        warnings.append("Book rows are fresh but usable samples are missing or stale.")

    return warnings


def build_collector_status_payload(
    state: RunState,
    *,
    settings,
    ws_runtime: dict[str, object] | None = None,
    last_book_sample_at_ms: int | None = None,
    last_book_row_at_ms: int | None = None,
    trades_without_reference_mid: int = 0,
    health_warnings: list[str] | None = None,
    last_sync_error: str | None = None,
    consecutive_sync_failures: int = 0,
    last_sync_attempt_at: str | None = None,
) -> dict[str, object]:
    """Collector-only health JSON (never writes ranking aggregates)."""
    warnings = list(health_warnings or [])
    warnings.extend(_ws_degraded(ws_runtime))
    warnings.extend(
        _book_health_warnings(
            settings=settings,
            last_book_sample_at_ms=last_book_sample_at_ms,
            last_book_row_at_ms=last_book_row_at_ms,
            started_at=state.started_at,
            startup_grace_minutes=settings.collector_startup_grace_minutes,
        )
    )
    if last_sync_error:
        warnings.append(f"Durable sync failed: {last_sync_error}")
    if consecutive_sync_failures > 0 and not state.last_successful_flush:
        warnings.append(
            f"Durable sync has failed {consecutive_sync_failures} time(s) with no successful flush."
        )
    degraded = bool(_ws_degraded(ws_runtime)) or any(
        "stale" in w.lower() or "usable" in w.lower() or "sync failed" in w.lower()
        for w in warnings
    )
    status = collector_status_label(
        state,
        ok_minutes=settings.status_ok_minutes,
        warn_minutes=settings.status_warn_minutes,
        degraded=degraded,
        startup_grace_minutes=settings.collector_startup_grace_minutes,
        last_sync_attempt_at=last_sync_attempt_at,
        consecutive_sync_failures=consecutive_sync_failures,
    )
    return {
        "run_id": state.run_id,
        "status": status,
        "started_at": state.started_at,
        "ended_at": state.ended_at,
        "generated_at": datetime.now(UTC).isoformat(),
        "last_successful_sync": state.last_successful_flush,
        "last_sync_attempt_at": last_sync_attempt_at,
        "last_sync_error": last_sync_error,
        "consecutive_sync_failures": consecutive_sync_failures,
        "samples_written": state.samples_written,
        "trades_written": state.trades_written,
        "markouts_written": state.markouts_written,
        "last_trade_at": _ms_to_iso(state.last_trade_timestamp_ms),
        "last_usable_book_sample_at": _ms_to_iso(last_book_sample_at_ms),
        "last_book_row_at": _ms_to_iso(last_book_row_at_ms),
        "trades_without_reference_mid": trades_without_reference_mid,
        "ws": ws_runtime,
        "health_warnings": warnings,
        "git_sha": state.git_sha or settings.git_sha,
        "collector_version": state.collector_version or settings.collector_version,
    }
