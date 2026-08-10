"""Analysis target resolution and durable watermark helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from lighter_mm.analytics.parquet_source import AnalysisSources
from lighter_mm.config import Settings
from lighter_mm.storage.parquet_validation import discover_parquet_files, parquet_max_timestamp_ms
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


def _infer_event_watermark_from_sources(sources: AnalysisSources) -> int | None:
    """Infer durable event watermark from uploaded Parquet metadata (legacy runs)."""
    max_ts: int | None = None
    for root in (sources.books, sources.trades, sources.markouts):
        if not root.is_dir():
            continue
        for path in discover_parquet_files(root):
            ts = parquet_max_timestamp_ms(path)
            if ts is None:
                continue
            max_ts = ts if max_ts is None else max(max_ts, ts)
    return max_ts


def _durable_watermark_ms(
    state: RunState,
    *,
    sources: AnalysisSources | None = None,
) -> int | None:
    """Return the latest durable market-event timestamp, never sync wall-clock time."""
    if state.last_durable_event_ms is not None:
        return int(state.last_durable_event_ms)
    if sources is not None:
        return _infer_event_watermark_from_sources(sources)
    return None


def _analysis_window_ms(
    state: RunState,
    *,
    execution_start_ms: int,
    sources: AnalysisSources | None = None,
    window_hours: float | None = None,
) -> tuple[int, int, int, int]:
    """Return (start_ms, end_ms, analysis_end_ms, durable_watermark_ms).

    When ``window_hours`` > 0, start is clamped to a rolling window ending at
    ``end_ms`` (never before ``run.started_at``). Collector retention remains
    independent (e.g. 72h); this only bounds Analyzer ranking recomputation.
    ``window_hours`` 0/None keeps legacy full-history behavior.
    """
    run_start_ms = _iso_to_ms(state.started_at) or execution_start_ms
    durable_ms = _durable_watermark_ms(state, sources=sources)
    if durable_ms is None:
        raise RuntimeError(
            "missing last_durable_event_ms and unable to infer event watermark from "
            "durable Parquet; refusing to analyze beyond known market data"
        )

    if state.status == "completed" and state.ended_at:
        ended_ms = _iso_to_ms(state.ended_at) or durable_ms
        end_ms = min(ended_ms, durable_ms)
    else:
        end_ms = min(execution_start_ms, durable_ms)

    start_ms = run_start_ms
    if window_hours is not None and float(window_hours) > 0:
        window_ms = int(float(window_hours) * 3600.0 * 1000.0)
        start_ms = max(run_start_ms, end_ms - window_ms)

    return start_ms, end_ms, end_ms, durable_ms
