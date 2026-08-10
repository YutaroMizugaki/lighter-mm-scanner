"""Dashboard collector vs analysis health separation (logic + source checks)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_TS = ROOT / "dashboard" / "lib" / "data.ts"
HOME_PAGE = ROOT / "dashboard" / "app" / "page.tsx"
DATA_FRESHNESS = ROOT / "dashboard" / "components" / "DataFreshness.tsx"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _effective_collector_status(
    last_sync: str | None,
    last_event: str | None = None,
    baked: str = "COLLECTING",
    ok_minutes: float = 20,
    warn_minutes: float = 40,
    sync_failures: int = 0,
) -> str:
    """Mirror dashboard/lib/data.ts effectiveCollectorStatus."""
    if baked in ("COMPLETED", "ERROR"):
        return baked
    if not last_event:
        if sync_failures > 0:
            return "DEGRADED"
        return "STALE"
    age_min = (
        datetime.now(UTC) - datetime.fromisoformat(last_event.replace("Z", "+00:00"))
    ).total_seconds() / 60
    if age_min > warn_minutes:
        return "OFFLINE"
    if age_min > ok_minutes:
        return "STALE"
    if sync_failures > 0 or baked == "DEGRADED":
        return "DEGRADED"
    return baked


def _effective_analysis_status(
    status: str | None,
    generated_at: str | None,
    last_ok: str | None = None,
    started_at: str | None = None,
    stale_minutes: float = 30,
    running_stale_minutes: float = 30,
) -> str:
    """Mirror dashboard/lib/data.ts effectiveAnalysisStatus."""
    if status is None:
        return "NOT_STARTED"
    if status == "NOT_STARTED":
        return "NOT_STARTED"
    if status == "NO_ACTIVE_RUN":
        return "NO_ACTIVE_RUN"
    if status == "ERROR":
        return "ERROR"
    if status == "RUNNING":
        running_stamp = started_at or generated_at
        if running_stamp:
            age_min = (
                datetime.now(UTC) - datetime.fromisoformat(running_stamp.replace("Z", "+00:00"))
            ).total_seconds() / 60
            if age_min > running_stale_minutes:
                return "STALE"
        return "RUNNING"
    stamp = last_ok or generated_at
    if not stamp:
        return status
    age_min = (
        datetime.now(UTC) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    ).total_seconds() / 60
    stale = age_min > stale_minutes
    if status == "DEGRADED":
        return "STALE" if stale else "DEGRADED"
    if stale and status == "OK":
        return "STALE"
    if status == "OK":
        return "OK"
    return status


# Test A — Collector fresh / Analyzer fresh → COLLECTING + OK
def test_collector_fresh_analyzer_fresh() -> None:
    now = datetime.now(UTC)
    event = _iso(now - timedelta(minutes=5))
    analysis_ts = _iso(now - timedelta(minutes=10))
    assert _effective_collector_status(_iso(now - timedelta(minutes=5)), event) == "COLLECTING"
    assert _effective_analysis_status("OK", analysis_ts, analysis_ts) == "OK"


# Test B — Collector fresh / Analyzer >30m stale → COLLECTING + STALE
def test_collector_fresh_analyzer_stale() -> None:
    now = datetime.now(UTC)
    event = _iso(now - timedelta(minutes=5))
    analysis_ts = _iso(now - timedelta(minutes=45))
    assert _effective_collector_status(_iso(now - timedelta(minutes=5)), event) == "COLLECTING"
    assert _effective_analysis_status("OK", analysis_ts, analysis_ts) == "STALE"


# Test C — Collector fresh / Analyzer ERROR
def test_collector_fresh_analyzer_error() -> None:
    now = datetime.now(UTC)
    event = _iso(now - timedelta(minutes=5))
    assert _effective_collector_status(_iso(now - timedelta(minutes=5)), event) == "COLLECTING"
    assert _effective_analysis_status("ERROR", _iso(now), None) == "ERROR"


# Test D — Analyzer stale does not make Collector OFFLINE
def test_analyzer_stale_does_not_offline_collector() -> None:
    now = datetime.now(UTC)
    event = _iso(now - timedelta(minutes=5))
    analysis_ts = _iso(now - timedelta(hours=2))
    assert _effective_collector_status(_iso(now - timedelta(minutes=5)), event) == "COLLECTING"
    assert _effective_analysis_status("OK", analysis_ts, analysis_ts) == "STALE"


# Test E/F/G — UI labels in source
def test_dashboard_labels_last_analysis_and_last_sync() -> None:
    home = HOME_PAGE.read_text(encoding="utf-8")
    data_freshness = DATA_FRESHNESS.read_text(encoding="utf-8")
    data = DATA_TS.read_text(encoding="utf-8")
    diagnostics = (ROOT / "dashboard" / "components" / "Diagnostics.tsx").read_text(
        encoding="utf-8",
    )
    status = (ROOT / "dashboard" / "lib" / "status.ts").read_text(encoding="utf-8")
    types = (ROOT / "dashboard" / "lib" / "types.ts").read_text(encoding="utf-8")
    # Home delegates analysis freshness to DataFreshness (not inline "Last Analysis").
    assert "DataFreshness" in home
    assert "lastAnalysisAt" in home
    assert "publicFreshnessCopy" in data_freshness
    assert "lastAnalysisAt" in data_freshness
    assert "Last Sync" in diagnostics
    assert "Last Analysis" in diagnostics
    assert "Last Data" not in home
    assert "Last Update" not in home
    assert "analysisDisplayTimestamp" in data
    assert "last_successful_sync" in types
    assert "last_durable_event_at" in status


def test_dashboard_no_last_update_label() -> None:
    home = HOME_PAGE.read_text(encoding="utf-8")
    assert "Last Update" not in home


def test_analysis_not_started_when_status_missing() -> None:
    assert _effective_analysis_status(None, None) == "NOT_STARTED"


def test_analysis_no_active_run_status() -> None:
    assert _effective_analysis_status("NO_ACTIVE_RUN", _iso(datetime.now(UTC)), None) == "NO_ACTIVE_RUN"


def test_running_over_30m_becomes_stale() -> None:
    now = datetime.now(UTC)
    old = _iso(now - timedelta(minutes=45))
    assert _effective_analysis_status("RUNNING", old, started_at=old) == "STALE"


def test_degraded_over_30m_becomes_stale() -> None:
    now = datetime.now(UTC)
    old = _iso(now - timedelta(minutes=45))
    assert _effective_analysis_status("DEGRADED", old, old) == "STALE"
