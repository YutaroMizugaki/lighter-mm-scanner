"""Core runtime verification checks."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from lighter_mm.cloud.analysis_outcome import is_analysis_success, is_stale_running
from lighter_mm.runtime_verify.models import (
    CheckLevel,
    CheckResult,
    RuntimeSnapshot,
    VerifyReport,
)
from lighter_mm.runtime_verify.time_utils import (
    age_seconds,
    format_age,
    format_ts_dual,
    parse_iso,
    parse_ms,
)


def _check(level: CheckLevel, section: str, name: str, message: str) -> CheckResult:
    return CheckResult(name=name, level=level, message=message, section=section)


def _sha_from_image(image: str | None) -> str | None:
    if not image:
        return None
    # .../collector:abc123 or @sha256:...
    if ":" in image.rsplit("/", 1)[-1]:
        tag = image.rsplit(":", 1)[-1]
        if tag.startswith("sha256:"):
            return None
        if re.fullmatch(r"[0-9a-f]{7,40}", tag):
            return tag
    return None


def _parse_json(raw: str | None) -> tuple[dict[str, Any] | None, bool]:
    if not raw:
        return None, False
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data, True
        return None, False
    except json.JSONDecodeError:
        return None, False


def _freshness_level(
    age_s: float | None,
    ok_minutes: float,
    warn_minutes: float,
) -> CheckLevel:
    if age_s is None:
        return CheckLevel.FAIL
    if age_s <= ok_minutes * 60:
        return CheckLevel.PASS
    if age_s <= warn_minutes * 60:
        return CheckLevel.WARN
    return CheckLevel.FAIL


def verify_runtime(snapshot: RuntimeSnapshot, now: datetime | None = None) -> VerifyReport:
    now_dt = now or datetime.now(UTC)
    report = VerifyReport(
        status="healthy",
        expected_git_sha=snapshot.expected_git_sha,
        expected_sha_source=snapshot.expected_sha_source,
    )

    _verify_deployment(report, snapshot)
    _verify_collector(report, snapshot, now_dt)
    _verify_websocket(report, snapshot)
    _verify_storage(report, snapshot, now_dt)
    _verify_analyzer(report, snapshot, now_dt)
    _verify_public_data(report, snapshot, now_dt)
    _verify_dashboard(report, snapshot)
    _collect_timestamps(report, snapshot)
    _collect_run_ids(report, snapshot)
    report.finalize_status()
    return report


def _verify_deployment(report: VerifyReport, snap: RuntimeSnapshot) -> None:
    section = "Deployment"
    exp = snap.expected_git_sha

    if snap.worker_pool.exists:
        report.add(_check(CheckLevel.PASS, section, "worker_pool.exists", "exists"))
        wp_sha = snap.worker_pool.git_sha or _sha_from_image(snap.worker_pool.image)
        if wp_sha:
            msg = wp_sha
            if exp and wp_sha != exp:
                report.add(
                    _check(CheckLevel.FAIL, section, "worker_pool.revision", f"{msg} != expected {exp}")
                )
            else:
                report.add(_check(CheckLevel.PASS, section, "worker_pool.revision", msg))
        else:
            report.add(_check(CheckLevel.WARN, section, "worker_pool.revision", "could not resolve SHA"))
        if snap.worker_pool.image:
            report.add(_check(CheckLevel.PASS, section, "worker_pool.image", snap.worker_pool.image))
    else:
        report.add(_check(CheckLevel.FAIL, section, "worker_pool.exists", "not found"))

    if snap.analyzer_job.exists:
        report.add(_check(CheckLevel.PASS, section, "analyzer_job.exists", "exists"))
        aj_sha = snap.analyzer_job.git_sha or _sha_from_image(snap.analyzer_job.image)
        if aj_sha:
            if exp and aj_sha != exp:
                report.add(
                    _check(CheckLevel.FAIL, section, "analyzer_job.revision", f"{aj_sha} != expected {exp}")
                )
            else:
                report.add(_check(CheckLevel.PASS, section, "analyzer_job.revision", aj_sha))
        cmd = snap.analyzer_job.command
        args = snap.analyzer_job.args
        if cmd and "lighter-mm" in cmd and "cloud-analyze" in args:
            report.add(_check(CheckLevel.PASS, section, "analyzer_job.command", "lighter-mm cloud-analyze"))
        else:
            report.add(
                _check(
                    CheckLevel.WARN,
                    section,
                    "analyzer_job.command",
                    f"command={cmd} args={args}",
                )
            )
    else:
        report.add(_check(CheckLevel.FAIL, section, "analyzer_job.exists", "not found"))

    if snap.scheduler.exists:
        report.add(_check(CheckLevel.PASS, section, "scheduler.target", snap.scheduler.target_uri or "ok"))
    else:
        report.add(_check(CheckLevel.WARN, section, "scheduler.target", "scheduler job not found"))


def _verify_collector(report: VerifyReport, snap: RuntimeSnapshot, now: datetime) -> None:
    section = "Collector"
    data, valid = snap.collector_status, bool(snap.collector_status)
    if snap.collector_status_raw and not valid:
        report.add(_check(CheckLevel.FAIL, section, "collector_status.json", "malformed JSON"))
        return
    if not data:
        report.add(_check(CheckLevel.FAIL, section, "collector_status.json", "missing"))
        return

    report.add(_check(CheckLevel.PASS, section, "collector_status.json", "valid"))
    status = str(data.get("status") or "")
    if status.upper() == "ERROR":
        report.add(_check(CheckLevel.FAIL, section, "collector.status", status))
    else:
        report.add(_check(CheckLevel.PASS, section, "collector.status", status or "ok"))

    pub_sha = str(data.get("git_sha") or "")
    wp_sha = snap.worker_pool.git_sha or _sha_from_image(snap.worker_pool.image)
    if pub_sha and wp_sha and pub_sha != wp_sha:
        report.add(
            _check(
                CheckLevel.FAIL,
                section,
                "collector.revision",
                f"status git_sha={pub_sha} != worker pool {wp_sha}",
            )
        )
    elif pub_sha and snap.expected_git_sha and pub_sha != snap.expected_git_sha:
        report.add(
            _check(
                CheckLevel.FAIL,
                section,
                "collector.revision",
                f"status git_sha={pub_sha} != expected {snap.expected_git_sha}",
            )
        )
    elif pub_sha:
        report.add(_check(CheckLevel.PASS, section, "collector.revision", pub_sha))

    durable_at = parse_iso(data.get("last_durable_event_at"))
    durable_age = age_seconds(durable_at, now)
    durable_lvl = _freshness_level(durable_age, snap.status_ok_minutes, snap.status_warn_minutes)
    report.add(
        _check(
            durable_lvl,
            section,
            "durable_data_age",
            f"latest durable data {format_age(durable_age)} ago",
        )
    )

    sync_at = parse_iso(data.get("last_successful_sync"))
    sync_age = age_seconds(sync_at, now)
    sync_lvl = _freshness_level(sync_age, snap.status_ok_minutes, snap.status_warn_minutes)
    report.add(
        _check(
            sync_lvl,
            section,
            "sync_age",
            f"last successful sync {format_age(sync_age)} ago",
        )
    )

    if durable_lvl == CheckLevel.FAIL and sync_lvl == CheckLevel.PASS:
        report.add(
            _check(
                CheckLevel.FAIL,
                section,
                "durable_vs_sync",
                "sync fresh but durable event stale — collector not healthy",
            )
        )

    sync_err = data.get("last_sync_error")
    failures = int(data.get("consecutive_sync_failures") or 0)
    if sync_err:
        report.add(_check(CheckLevel.WARN, section, "last_sync_error", str(sync_err)))
    if failures > 0:
        report.add(_check(CheckLevel.WARN, section, "sync_failures", f"{failures} consecutive"))


def _verify_websocket(report: VerifyReport, snap: RuntimeSnapshot) -> None:
    section = "WebSocket"
    data = snap.collector_status or {}
    ws = data.get("ws") or {}
    if not ws:
        report.add(_check(CheckLevel.WARN, section, "ws", "no ws block in collector_status"))
        return

    connected = int(ws.get("connected_shards") or 0)
    total = int(ws.get("total_shards") or 0)
    planned = int(ws.get("planned_channels") or 0)
    acked = int(ws.get("acked_channels") or 0)
    subscribed = int(ws.get("subscribed_channels") or 0)
    last_err = ws.get("last_ws_error")

    if connected == 0:
        report.add(_check(CheckLevel.FAIL, section, "connected_shards", f"{connected}/{total}"))
    elif connected < total:
        report.add(_check(CheckLevel.WARN, section, "connected_shards", f"{connected}/{total}"))
    else:
        report.add(_check(CheckLevel.PASS, section, "connected_shards", f"{connected}/{total}"))

    report.add(_check(CheckLevel.PASS, section, "subscriptions", f"{subscribed}/{planned} acked {acked}"))
    if acked < planned:
        report.add(_check(CheckLevel.WARN, section, "acked_channels", f"{acked}/{planned}"))
    if last_err and connected > 0:
        report.add(_check(CheckLevel.WARN, section, "last_ws_error", str(last_err)))


def _verify_storage(report: VerifyReport, snap: RuntimeSnapshot, now: datetime) -> None:
    section = "Storage"
    pb = snap.parquet_book
    data = snap.collector_status or {}

    if not pb.object_path:
        report.add(_check(CheckLevel.FAIL, section, "latest_book_parquet", "missing"))
        return

    if pb.size_bytes <= 0:
        report.add(_check(CheckLevel.FAIL, section, "latest_book_parquet", "zero bytes"))
        return

    if pb.object_path.endswith(".tmp"):
        report.add(_check(CheckLevel.FAIL, section, "latest_book_parquet", "points to .tmp object"))
        return

    age = age_seconds(pb.updated_at, now)
    report.add(
        _check(
            CheckLevel.PASS,
            section,
            "latest_book_parquet",
            f"{format_age(age)} ago ({pb.object_path})",
        )
    )

    if not pb.valid:
        report.add(_check(CheckLevel.FAIL, section, "parquet_validation", pb.error or "invalid"))
        return

    rows = pb.row_count or 0
    report.add(_check(CheckLevel.PASS, section, "parquet_validation", f"{rows:,} rows"))

    durable_at = parse_iso(data.get("last_durable_event_at"))
    durable_ms = parse_ms(data.get("last_durable_event_ms")) or (
        int(durable_at.timestamp() * 1000) if durable_at else None
    )
    max_ms = pb.max_timestamp_ms
    if durable_ms is not None and max_ms is not None:
        gap_s = abs(durable_ms - max_ms) / 1000.0
        fail_s = max(900.0, snap.parquet_rotation_minutes * 60)
        if gap_s <= 300:
            lvl = CheckLevel.PASS
        elif gap_s <= fail_s:
            lvl = CheckLevel.WARN
        else:
            lvl = CheckLevel.FAIL
        report.add(
            _check(
                lvl,
                section,
                "parquet_event_watermark",
                f"gap {int(gap_s)}s (durable vs parquet MAX)",
            )
        )


def _verify_analyzer(report: VerifyReport, snap: RuntimeSnapshot, now: datetime) -> None:
    section = "Analyzer"
    exp = snap.expected_git_sha
    executions = snap.executions

    latest = executions[0] if executions else None
    latest_ok = next((e for e in executions if e.succeeded), None)

    # Cloud Run Job COMPLETE ≠ analysis success. Report job completion separately.
    if latest and latest.failed and not latest_ok:
        report.add(_check(CheckLevel.FAIL, section, "latest_execution", f"{latest.name} FAILED"))
    elif latest and latest.succeeded:
        report.add(
            _check(
                CheckLevel.PASS,
                section,
                "latest_execution",
                f"{latest.name} COMPLETE (Cloud Run Job exit; not analysis outcome)",
            )
        )
    elif latest and latest.running:
        report.add(_check(CheckLevel.WARN, section, "latest_execution", f"{latest.name} RUNNING"))
    elif latest:
        report.add(_check(CheckLevel.WARN, section, "latest_execution", latest.name))
    else:
        report.add(_check(CheckLevel.WARN, section, "latest_execution", "no executions found"))

    interval = snap.analysis_interval_minutes
    ok_s = interval * 2 * 60
    warn_s = interval * 3 * 60
    if latest_ok and latest_ok.completed_at:
        ok_age = age_seconds(latest_ok.completed_at, now)
        if ok_age is None:
            lvl = CheckLevel.WARN
        elif ok_age <= ok_s:
            lvl = CheckLevel.PASS
        elif ok_age <= warn_s:
            lvl = CheckLevel.WARN
        else:
            lvl = CheckLevel.FAIL
        report.add(
            _check(
                lvl,
                section,
                "last_successful_execution",
                f"{format_age(ok_age)} ago (Cloud Run Job; not analysis outcome)",
            )
        )

    data, valid = snap.analysis_status, bool(snap.analysis_status)
    if snap.analysis_status_raw and not valid:
        report.add(_check(CheckLevel.FAIL, section, "analysis_status.json", "malformed JSON"))
        return

    analysis_ok = is_analysis_success(data if valid else None, snap.current_json)

    if not data:
        # Do not treat Cloud Run COMPLETE as proof analysis_status should exist.
        report.add(
            _check(
                CheckLevel.WARN,
                section,
                "analysis_status.json",
                "missing (analyzer not run yet; Cloud Run COMPLETE ≠ analysis success)",
            )
        )
        return

    ast = str(data.get("status") or "")
    if analysis_ok:
        report.add(
            _check(
                CheckLevel.PASS,
                section,
                "analysis.outcome",
                f"{ast} with last_successful_analysis_at and current.json",
            )
        )
    elif ast == "RUNNING":
        stale = is_stale_running(data, stale_minutes=float(interval * 2), now=now)
        started = parse_iso(data.get("started_at") or data.get("generated_at"))
        run_age = age_seconds(started, now)
        if stale or (run_age and run_age > interval * 3 * 60):
            report.add(
                _check(
                    CheckLevel.FAIL,
                    section,
                    "analysis.outcome",
                    f"stale RUNNING {format_age(run_age)}; current.json missing or incomplete",
                )
            )
        else:
            report.add(
                _check(
                    CheckLevel.WARN,
                    section,
                    "analysis.outcome",
                    "RUNNING (not analysis success until OK/DEGRADED + current.json)",
                )
            )
    elif latest_ok:
        report.add(
            _check(
                CheckLevel.WARN,
                section,
                "analysis.outcome",
                "Cloud Run COMPLETE but analysis success criteria not met "
                "(need status OK/DEGRADED, last_successful_analysis_at, current.json)",
            )
        )
    else:
        report.add(
            _check(
                CheckLevel.WARN,
                section,
                "analysis.outcome",
                f"{ast or 'unknown'} (not analysis success)",
            )
        )

    if ast == "ERROR":
        report.add(_check(CheckLevel.FAIL, section, "analysis.status", ast))
    elif ast == "DEGRADED":
        report.add(_check(CheckLevel.WARN, section, "analysis.status", ast))
    elif ast == "RUNNING":
        started = parse_iso(data.get("started_at") or data.get("generated_at"))
        run_age = age_seconds(started, now)
        if run_age and run_age > interval * 3 * 60:
            report.add(_check(CheckLevel.FAIL, section, "analysis.status", f"RUNNING {format_age(run_age)}"))
        else:
            report.add(_check(CheckLevel.WARN, section, "analysis.status", ast))
    elif ast == "OK":
        gen = parse_iso(data.get("generated_at"))
        gen_age = age_seconds(gen, now)
        lvl = _freshness_level(gen_age, interval * 2, interval * 3)
        report.add(_check(lvl, section, "analysis.status", f"OK ({format_age(gen_age)} ago)"))
    else:
        report.add(_check(CheckLevel.WARN, section, "analysis.status", ast or "unknown"))

    a_sha = str(data.get("git_sha") or "")
    if a_sha and exp and a_sha == exp:
        report.add(_check(CheckLevel.PASS, section, "analysis.revision", a_sha))
    elif a_sha and exp and a_sha != exp:
        # Cloud Run Job COMPLETE alone is not proof the new revision analyzed.
        # Keep this as WARN until analysis_status.git_sha matches the expected revision.
        report.add(
            _check(
                CheckLevel.WARN,
                section,
                "analysis.revision",
                f"{a_sha} — new analyzer revision has not produced analysis yet",
            )
        )
    elif a_sha:
        report.add(_check(CheckLevel.PASS, section, "analysis.revision", a_sha))


def _verify_public_data(report: VerifyReport, snap: RuntimeSnapshot, now: datetime) -> None:
    section = "Public data"
    current, valid = snap.current_json, bool(snap.current_json)
    if snap.current_json_raw and not valid:
        report.add(_check(CheckLevel.FAIL, section, "current.json", "malformed"))
        return
    if not current:
        report.add(_check(CheckLevel.FAIL, section, "current.json", "missing"))
        return

    report.add(_check(CheckLevel.PASS, section, "current.json", "ok"))
    analysis_id = str(current.get("analysis_id") or "")
    if not analysis_id:
        report.add(_check(CheckLevel.FAIL, section, "analysis_id", "missing"))
        return

    prefix = snap.public_prefix.rstrip("/")
    gen_latest = snap.generation_files.get(f"{prefix}/generations/{analysis_id}/latest.json")
    gen_markets = snap.generation_files.get(f"{prefix}/generations/{analysis_id}/markets.json")
    latest_valid = False
    latest_data: dict[str, Any] | None = None

    if not gen_latest:
        report.add(_check(CheckLevel.FAIL, section, "generation.latest.json", "missing"))
    else:
        report.add(_check(CheckLevel.PASS, section, "generation.latest.json", "ok"))
        latest_data, latest_valid = _parse_json(gen_latest)
        if not latest_valid:
            report.add(_check(CheckLevel.FAIL, section, "generation.latest.json", "malformed"))
        elif latest_data:
            # latest.json exposes markets as a count (int), not row objects.
            # Row-level coverage is validated via generations/.../markets.json below.
            markets_field = latest_data.get("markets")
            if isinstance(markets_field, list):
                _check_markets_coverage(report, section, markets_field, "latest")
            elif isinstance(markets_field, int):
                if markets_field <= 0:
                    report.add(
                        _check(
                            CheckLevel.FAIL,
                            section,
                            "generation.latest.markets_count",
                            f"markets={markets_field}",
                        )
                    )
                else:
                    report.add(
                        _check(
                            CheckLevel.PASS,
                            section,
                            "generation.latest.markets_count",
                            f"markets={markets_field}",
                        )
                    )
            analyzed = latest_data.get("markets_analyzed")
            if isinstance(analyzed, int) and analyzed <= 0:
                report.add(
                    _check(
                        CheckLevel.FAIL,
                        section,
                        "generation.latest.markets_analyzed",
                        f"markets_analyzed={analyzed}",
                    )
                )

    if not gen_markets:
        report.add(_check(CheckLevel.FAIL, section, "generation.markets.json", "missing"))
    else:
        report.add(_check(CheckLevel.PASS, section, "generation.markets.json", "ok"))
        markets_data, markets_valid = _parse_json(gen_markets)
        if not markets_valid:
            report.add(_check(CheckLevel.FAIL, section, "generation.markets.json", "malformed"))
        elif markets_data:
            rows = markets_data.get("markets") or markets_data
            if isinstance(rows, list):
                _check_markets_coverage(report, section, rows, "markets")

    cur_gen = parse_iso(current.get("generated_at"))
    if gen_latest and latest_valid and cur_gen:
        ld, lv = _parse_json(gen_latest)
        if lv and ld:
            lg = parse_iso(ld.get("generated_at"))
            if lg and abs((cur_gen - lg).total_seconds()) > 120:
                report.add(
                    _check(CheckLevel.WARN, section, "generation.consistency", "generated_at mismatch")
                )
            else:
                report.add(_check(CheckLevel.PASS, section, "generation.consistency", "ok"))


def _check_markets_coverage(
    report: VerifyReport,
    section: str,
    rows: list[Any],
    label: str,
) -> None:
    cov_fields = (
        "data_coverage_pct",
        "observation_coverage_pct",
        "usable_quote_coverage_pct",
        "spread_coverage_pct",
    )
    required = ("market_id", "symbol", "score") if label == "markets" else ()
    for i, row in enumerate(rows[:50]):
        if not isinstance(row, dict):
            report.add(_check(CheckLevel.FAIL, section, f"coverage.schema.{label}", f"row {i} not object"))
            continue
        for f in required:
            if f not in row:
                report.add(_check(CheckLevel.FAIL, section, f"coverage.schema.{label}", f"missing {f}"))
        for f in cov_fields:
            if f not in row:
                continue
            v = row[f]
            if isinstance(v, str) and v.lower() in ("nan", "inf", "-inf", "infinity"):
                report.add(_check(CheckLevel.FAIL, section, f"coverage.{f}", f"bad string {v}"))
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                report.add(_check(CheckLevel.FAIL, section, f"coverage.{f}", f"non-numeric {v}"))
                continue
            if math.isnan(fv) or math.isinf(fv):
                report.add(_check(CheckLevel.FAIL, section, f"coverage.{f}", "NaN/Infinity"))
            elif fv < 0 or fv > 100:
                report.add(_check(CheckLevel.FAIL, section, f"coverage.{f}", f"out of range {fv}"))
        obs = row.get("observation_coverage_pct")
        data = row.get("data_coverage_pct")
        if obs is not None and data is not None:
            try:
                if abs(float(obs) - float(data)) > 2.0:
                    report.add(
                        _check(
                            CheckLevel.WARN,
                            section,
                            "coverage.obs_vs_data",
                            f"obs={obs} data={data}",
                        )
                    )
            except (TypeError, ValueError):
                pass
    report.add(
        _check(CheckLevel.PASS, section, "coverage.schema", f"{label} ok ({min(len(rows), 50)} checked)")
    )


def _verify_dashboard(report: VerifyReport, snap: RuntimeSnapshot) -> None:
    section = "Dashboard"
    if snap.dashboard_http_status is None:
        return
    if snap.dashboard_http_status == 200:
        body = snap.dashboard_body or ""
        if "Lighter" in body or "lighter" in body.lower():
            report.add(_check(CheckLevel.PASS, section, "vercel_http", "200"))
        else:
            report.add(_check(CheckLevel.WARN, section, "vercel_http", "200 but marker not found"))
    else:
        report.add(_check(CheckLevel.FAIL, section, "vercel_http", str(snap.dashboard_http_status)))


def _collect_timestamps(report: VerifyReport, snap: RuntimeSnapshot) -> None:
    c = snap.collector_status or {}
    a = snap.analysis_status or {}
    cur = snap.current_json or {}
    report.timestamps = {
        "last_durable_data": format_ts_dual(parse_iso(c.get("last_durable_event_at"))),
        "last_successful_sync": format_ts_dual(parse_iso(c.get("last_successful_sync"))),
        "collector_status_generated": format_ts_dual(parse_iso(c.get("generated_at"))),
        "last_analyzer_execution": format_ts_dual(
            snap.executions[0].completed_at if snap.executions else None
        ),
        "last_successful_analysis": format_ts_dual(parse_iso(a.get("last_successful_analysis_at"))),
        "analysis_status_generated": format_ts_dual(parse_iso(a.get("generated_at"))),
        "public_generation_generated": format_ts_dual(parse_iso(cur.get("generated_at"))),
    }


def _collect_run_ids(report: VerifyReport, snap: RuntimeSnapshot) -> None:
    c = snap.collector_status or {}
    a = snap.analysis_status or {}
    collector_run = str(c.get("run_id") or "")
    analysis_run = str(a.get("run_id") or "")
    latest_run = ""
    if snap.current_json and snap.generation_files:
        aid = str(snap.current_json.get("analysis_id") or "")
        prefix = snap.public_prefix.rstrip("/")
        raw = snap.generation_files.get(f"{prefix}/generations/{aid}/latest.json")
        if raw:
            data, ok = _parse_json(raw)
            if ok and data:
                latest_run = str(data.get("run_id") or "")
    report.run_ids = {
        "collector": collector_run,
        "analysis": analysis_run,
        "latest_json": latest_run,
    }
    if collector_run and analysis_run and collector_run != analysis_run:
        # Cloud Run Job COMPLETE must not escalate this to FAIL; wait for formal
        # analysis success on the collector's current run_id.
        report.add(
            _check(
                CheckLevel.WARN,
                "Public data",
                "run_id.consistency",
                f"analysis is from previous run (collector={collector_run} analysis={analysis_run})",
            )
        )
