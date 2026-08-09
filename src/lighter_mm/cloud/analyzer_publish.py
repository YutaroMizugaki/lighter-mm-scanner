"""Analyzer dashboard publishing and analysis status."""

from __future__ import annotations

import logging
from typing import Any

from lighter_mm.cloud.sync import DurableSync

log = logging.getLogger(__name__)


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
