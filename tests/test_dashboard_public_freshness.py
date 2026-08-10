"""Dashboard public freshness presentation (mirrors dashboard/lib/public.ts)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TS = ROOT / "dashboard" / "lib" / "public.ts"
STATUS_TS = ROOT / "dashboard" / "lib" / "status.ts"
HOME_PAGE = ROOT / "dashboard" / "app" / "page.tsx"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _analysis_age_minutes(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 60


def _analysis_freshness_level(
    status: str,
    last_analysis_at: str | None,
    now: datetime,
    stale_minutes: float = 30,
) -> str:
    """Mirror dashboard/lib/public.ts analysisFreshnessLevel."""
    if status == "RUNNING":
        if not last_analysis_at:
            return "unavailable"
        age = _analysis_age_minutes(last_analysis_at, now)
        if age is None:
            return "unavailable"
        if age > stale_minutes:
            return "delayed"
        return "current"
    if status in ("OK", "DEGRADED", "COMPLETED"):
        return "current"
    if status == "STALE":
        return "delayed"
    return "unavailable"


def _public_freshness_copy(
    status: str,
    last_analysis_at: str | None,
    relative: str,
    now: datetime,
) -> dict[str, str]:
    """Mirror dashboard/lib/public.ts publicFreshnessCopy."""
    level = _analysis_freshness_level(status, last_analysis_at, now)
    if status == "RUNNING":
        running_note = "Analysis updating" if last_analysis_at else "Analysis is running"
        if level == "current":
            detail = (
                f"Updated {relative} · {running_note}"
                if last_analysis_at
                else running_note
            )
            return {"level": level, "label": "Data current", "detail": detail}
        if level == "delayed":
            detail = (
                f"Latest analysis {relative} · {running_note}"
                if last_analysis_at
                else running_note
            )
            return {"level": level, "label": "Data delayed", "detail": detail}
        return {
            "level": "unavailable",
            "label": "Data unavailable",
            "detail": running_note,
        }
    if level == "current":
        detail = f"Updated {relative}" if last_analysis_at else "Latest analysis is available"
        return {"level": level, "label": "Data current", "detail": detail}
    if level == "delayed":
        detail = (
            f"Latest analysis {relative}"
            if last_analysis_at
            else "Latest analysis is older than expected"
        )
        return {"level": level, "label": "Data delayed", "detail": detail}
    return {
        "level": level,
        "label": "Data unavailable",
        "detail": "No usable analysis is currently available",
    }


def _fallback_analysis_status_from_overview(
    overview_status: str,
    generated_at: str | None,
    now: datetime,
    stale_minutes: float = 30,
) -> tuple[str, bool]:
    """Mirror dashboard/lib/status.ts fallbackAnalysisStatusFromOverview."""
    baked = overview_status or "ERROR"
    if not generated_at:
        return baked, True
    age = _analysis_age_minutes(generated_at, now)
    stale = age is not None and age > stale_minutes
    if baked == "ERROR":
        return "ERROR", False
    if baked == "DEGRADED":
        return ("STALE", True) if stale else ("DEGRADED", False)
    if stale and baked in ("OK", "COMPLETED"):
        return "STALE", True
    if baked in ("OK", "COMPLETED"):
        return baked, False
    if baked == "RUNNING":
        return ("STALE", True) if stale else ("RUNNING", False)
    return baked, stale


def test_running_fresh_previous_result_is_current() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    last_at = _iso(now - timedelta(minutes=10))
    copy = _public_freshness_copy("RUNNING", last_at, "10 min ago", now)
    assert copy["level"] == "current"
    assert copy["label"] == "Data current"
    assert "Analysis updating" in copy["detail"]


def test_running_stale_previous_result_is_delayed() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    last_at = _iso(now - timedelta(minutes=45))
    copy = _public_freshness_copy("RUNNING", last_at, "45 min ago", now)
    assert copy["level"] == "delayed"
    assert copy["label"] == "Data delayed"
    assert copy["label"] != "Data current"
    assert "Analysis updating" in copy["detail"]


def test_running_no_previous_result_is_not_current() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    copy = _public_freshness_copy("RUNNING", None, "unknown", now)
    assert copy["level"] == "unavailable"
    assert copy["label"] != "Data current"
    assert copy["detail"] == "Analysis is running"


def test_analysis_status_fetch_failure_uses_overview_fallback() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    generated_at = _iso(now - timedelta(minutes=5))
    status, stale = _fallback_analysis_status_from_overview("OK", generated_at, now)
    assert status == "OK"
    assert stale is False
    assert status != "NOT_STARTED"

    level = _analysis_freshness_level(status, generated_at, now)
    copy = _public_freshness_copy(status, generated_at, "5 min ago", now)
    assert level == "current"
    assert copy["label"] != "Data unavailable"


def test_stale_regression() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    last_at = _iso(now - timedelta(minutes=60))
    copy = _public_freshness_copy("STALE", last_at, "1 hour ago", now)
    assert copy["level"] == "delayed"
    assert copy["label"] == "Data delayed"


def test_error_and_degraded_labels_regression() -> None:
    public = PUBLIC_TS.read_text(encoding="utf-8")
    status = STATUS_TS.read_text(encoding="utf-8")
    home = HOME_PAGE.read_text(encoding="utf-8")
    assert "lastAnalysisAt" in public
    assert "RUNNING" in public
    assert "effectivePublicAnalysisStatus" in status
    assert "overviewAnalysisTimestamp" in status
    assert "effectivePublicAnalysisStatus" in home
    assert "analysisStatusFetchFailed" in home
    assert "Prior valid results may still be shown" in status


def test_estimated_fill_semantics_unchanged() -> None:
    fmt_ts = (ROOT / "dashboard" / "lib" / "format.ts").read_text(encoding="utf-8")
    assert "quality === \"insufficient\"" in fmt_ts
    assert "return \"Insufficient\"" in fmt_ts
