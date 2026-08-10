"""Fetch runtime state from GCP (read-only gcloud)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lighter_mm.runtime_verify.models import (
    AnalyzerJobInfo,
    ExecutionInfo,
    ParquetBookInfo,
    RuntimeSnapshot,
    SchedulerInfo,
    WorkerPoolInfo,
)
from lighter_mm.runtime_verify.parquet_probe import probe_book_parquet
from lighter_mm.runtime_verify.time_utils import parse_iso


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _parse_json_out(raw: str) -> Any:
    if not raw.strip():
        return None
    return json.loads(raw)


def _env_git_sha(container: dict[str, Any]) -> str | None:
    for env in container.get("env") or []:
        if env.get("name") in ("GIT_SHA", "COMMIT_SHA", "LIGHTER_MM_GIT_SHA"):
            val = env.get("value")
            if val:
                return str(val)
    return None


def fetch_runtime_snapshot(
    *,
    project_id: str,
    region: str,
    gcs_bucket: str | None,
    gcs_public_bucket: str | None,
    worker_pool: str,
    analyzer_job: str,
    public_prefix: str = "lighter-mm/public",
    gcs_prefix: str = "lighter-mm",
    expected_git_sha: str | None = None,
    expected_sha_source: str = "unknown",
    dashboard_url: str | None = None,
    settings_overrides: dict[str, float] | None = None,
) -> RuntimeSnapshot:
    overrides = settings_overrides or {}
    snap = RuntimeSnapshot(
        expected_git_sha=expected_git_sha,
        expected_sha_source=expected_sha_source,
        gcs_bucket=gcs_bucket,
        gcs_public_bucket=gcs_public_bucket,
        public_prefix=public_prefix,
        analysis_interval_minutes=overrides.get("analysis_interval_minutes", 15.0),
        parquet_rotation_minutes=overrides.get("parquet_rotation_minutes", 15.0),
        gcs_upload_interval_minutes=overrides.get("gcs_upload_interval_minutes", 15.0),
        status_ok_minutes=overrides.get("status_ok_minutes", 20.0),
        status_warn_minutes=overrides.get("status_warn_minutes", 40.0),
    )

    wp_json = _run(
        [
            "gcloud",
            "run",
            "worker-pools",
            "describe",
            worker_pool,
            f"--project={project_id}",
            f"--region={region}",
            "--format=json",
        ]
    )
    if wp_json:
        wp = _parse_json_out(wp_json) or {}
        containers = (wp.get("template") or {}).get("containers") or [{}]
        c0 = containers[0] if containers else {}
        snap.worker_pool = WorkerPoolInfo(
            exists=True,
            image=c0.get("image"),
            git_sha=_env_git_sha(c0),
            instances=int((wp.get("scaling") or {}).get("manualInstanceCount") or 0),
            service_account=(wp.get("template") or {}).get("serviceAccount"),
        )

    job_json = _run(
        [
            "gcloud",
            "run",
            "jobs",
            "describe",
            analyzer_job,
            f"--project={project_id}",
            f"--region={region}",
            "--format=json",
        ]
    )
    if job_json:
        job = _parse_json_out(job_json) or {}
        tmpl = (job.get("template") or {}).get("template") or job.get("template") or {}
        containers = tmpl.get("containers") or []
        c0 = containers[0] if containers else {}
        snap.analyzer_job = AnalyzerJobInfo(
            exists=True,
            image=c0.get("image"),
            git_sha=_env_git_sha(c0),
            command=c0.get("command") or [],
            args=c0.get("args") or [],
            service_account=tmpl.get("serviceAccount"),
        )

    sch_json = _run(
        [
            "gcloud",
            "scheduler",
            "jobs",
            "describe",
            "lighter-mm-analyzer-schedule",
            f"--project={project_id}",
            f"--location={region}",
            "--format=json",
        ]
    )
    if sch_json:
        sch = _parse_json_out(sch_json) or {}
        snap.scheduler = SchedulerInfo(
            exists=True,
            schedule=sch.get("schedule"),
            target_uri=(sch.get("httpTarget") or {}).get("uri"),
        )

    exec_json = _run(
        [
            "gcloud",
            "run",
            "jobs",
            "executions",
            "list",
            f"--job={analyzer_job}",
            f"--project={project_id}",
            f"--region={region}",
            "--limit=5",
            "--format=json",
        ]
    )
    if exec_json:
        for item in _parse_json_out(exec_json) or []:
            snap.executions.append(
                ExecutionInfo(
                    name=item.get("name") or "",
                    started_at=parse_iso(item.get("createTime")),
                    completed_at=parse_iso(item.get("completionTime")),
                    succeeded=item.get("succeededCount", 0) > 0,
                    failed=item.get("failedCount", 0) > 0,
                    cancelled=item.get("cancelledCount", 0) > 0,
                    running=item.get("runningCount", 0) > 0,
                )
            )

    if gcs_public_bucket:
        prefix = public_prefix.rstrip("/")
        snap.collector_status_raw = _gcs_cat(gcs_public_bucket, f"{prefix}/collector_status.json")
        if snap.collector_status_raw:
            try:
                snap.collector_status = json.loads(snap.collector_status_raw)
            except json.JSONDecodeError:
                pass
        snap.analysis_status_raw = _gcs_cat(gcs_public_bucket, f"{prefix}/analysis_status.json")
        if snap.analysis_status_raw:
            try:
                snap.analysis_status = json.loads(snap.analysis_status_raw)
            except json.JSONDecodeError:
                pass
        snap.current_json_raw = _gcs_cat(gcs_public_bucket, f"{prefix}/current.json")
        if snap.current_json_raw:
            try:
                snap.current_json = json.loads(snap.current_json_raw)
            except json.JSONDecodeError:
                pass
        aid = (snap.current_json or {}).get("analysis_id")
        if aid:
            snap.generation_files[f"{prefix}/generations/{aid}/latest.json"] = _gcs_cat(
                gcs_public_bucket, f"{prefix}/generations/{aid}/latest.json"
            )
            snap.generation_files[f"{prefix}/generations/{aid}/markets.json"] = _gcs_cat(
                gcs_public_bucket, f"{prefix}/generations/{aid}/markets.json"
            )

    if gcs_bucket:
        snap.parquet_book = _fetch_latest_book(gcs_bucket, gcs_prefix)

    if dashboard_url:
        snap.dashboard_http_status, snap.dashboard_body = _http_get(dashboard_url)

    return snap


def _gcs_cat(bucket: str, object_path: str) -> str:
    return _run(["gcloud", "storage", "cat", f"gs://{bucket}/{object_path}"])


def _http_get(url: str) -> tuple[int | None, str | None]:
    try:
        import httpx

        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        return resp.status_code, resp.text
    except Exception:  # noqa: BLE001
        return None, None


def _fetch_latest_book(bucket: str, gcs_prefix: str) -> ParquetBookInfo:
    listing = _run(
        [
            "gcloud",
            "storage",
            "ls",
            "-l",
            f"gs://{bucket}/{gcs_prefix}/runs/*/books/**/*.parquet",
        ]
    )
    if not listing:
        listing = _run(
            [
                "gcloud",
                "storage",
                "ls",
                "-l",
                f"gs://{bucket}/{gcs_prefix}/**/books/**/*.parquet",
            ]
        )
    candidates: list[tuple[datetime, str, int]] = []
    for line in listing.splitlines():
        line = line.strip()
        if not line or line.endswith(".tmp") or ".parquet" not in line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        size = int(parts[0])
        ts_part = parts[1]
        path = parts[-1]
        if path.endswith(".tmp"):
            continue
        updated = parse_iso(ts_part) or datetime.now(UTC)
        candidates.append((updated, path.replace("gs://", ""), size))

    if not candidates:
        return ParquetBookInfo(error="no book parquet found")

    candidates.sort(key=lambda x: x[0], reverse=True)
    updated, object_path, size = candidates[0]
    info = ParquetBookInfo(
        object_path=object_path,
        updated_at=updated,
        size_bytes=size,
    )
    if size <= 0:
        info.error = "zero bytes"
        return info

    with tempfile.TemporaryDirectory(prefix="lighter-mm-verify-") as tmp:
        local = Path(tmp) / "book.parquet"
        _run(["gcloud", "storage", "cp", f"gs://{object_path}", str(local)])
        if not local.exists():
            info.error = "download failed"
            return info
        probed = probe_book_parquet(local)
        info.valid = probed.valid
        info.row_count = probed.row_count
        info.max_timestamp_ms = probed.max_timestamp_ms
        info.error = probed.error
    return info
