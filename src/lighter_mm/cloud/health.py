"""Collector and WebSocket health helpers for dashboard payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from lighter_mm.storage.state import RunState


def collector_status_label(
    state: RunState | None,
    *,
    ok_minutes: float,
    warn_minutes: float,
    degraded: bool = False,
) -> str:
    """Collector health label (independent of analysis freshness)."""
    if state is None:
        return "ERROR"
    if state.status == "completed":
        return "COMPLETED"
    if state.status == "error":
        return "ERROR"
    flush = state.last_successful_flush or state.updated_at or state.started_at
    if not flush:
        return "ERROR"
    try:
        ts = datetime.fromisoformat(flush)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except ValueError:
        return "ERROR"
    age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
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


def build_collector_status_payload(
    state: RunState,
    *,
    settings,
    ws_runtime: dict[str, object] | None = None,
    last_book_sample_at_ms: int | None = None,
    last_book_row_at_ms: int | None = None,
    trades_without_reference_mid: int = 0,
    health_warnings: list[str] | None = None,
) -> dict[str, object]:
    """Collector-only health JSON (never writes ranking aggregates)."""
    warnings = list(health_warnings or [])
    warnings.extend(_ws_degraded(ws_runtime))
    if last_book_sample_at_ms is not None:
        age_s = (datetime.now(UTC).timestamp() * 1000 - last_book_sample_at_ms) / 1000.0
        stale_threshold_s = max(settings.book_sample_interval_seconds * 3, 30)
        if age_s > stale_threshold_s:
            warnings.append(
                f"Usable book samples stale: last sample {age_s:.0f}s ago "
                f"(threshold {stale_threshold_s:.0f}s)."
            )
    degraded = bool(_ws_degraded(ws_runtime)) or any(
        "stale" in w.lower() for w in warnings
    )
    status = collector_status_label(
        state,
        ok_minutes=settings.status_ok_minutes,
        warn_minutes=settings.status_warn_minutes,
        degraded=degraded,
    )
    return {
        "run_id": state.run_id,
        "status": status,
        "started_at": state.started_at,
        "ended_at": state.ended_at,
        "generated_at": datetime.now(UTC).isoformat(),
        "last_successful_sync": state.last_successful_flush,
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
