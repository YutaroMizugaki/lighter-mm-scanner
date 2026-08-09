"""Analysis target resolution and durable watermark helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from lighter_mm.analytics.parquet_source import AnalysisSources
from lighter_mm.config import Settings
from lighter_mm.storage.state import RunState


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
