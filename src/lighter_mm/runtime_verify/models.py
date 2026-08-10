"""Data models for GCP runtime verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CheckLevel(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    level: CheckLevel
    message: str
    section: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "level": self.level.value,
            "message": self.message,
            "section": self.section,
        }


@dataclass
class WorkerPoolInfo:
    exists: bool = False
    image: str | None = None
    git_sha: str | None = None
    instances: int | None = None
    service_account: str | None = None


@dataclass
class AnalyzerJobInfo:
    exists: bool = False
    image: str | None = None
    git_sha: str | None = None
    command: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    service_account: str | None = None


@dataclass
class SchedulerInfo:
    exists: bool = False
    schedule: str | None = None
    target_uri: str | None = None


@dataclass
class ExecutionInfo:
    name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    succeeded: bool = False
    failed: bool = False
    cancelled: bool = False
    running: bool = False


@dataclass
class ParquetBookInfo:
    object_path: str | None = None
    updated_at: datetime | None = None
    size_bytes: int = 0
    max_timestamp_ms: int | None = None
    row_count: int | None = None
    valid: bool = False
    error: str | None = None


@dataclass
class RuntimeSnapshot:
    """Point-in-time inputs for verification (from GCP or test fixtures)."""

    expected_git_sha: str | None = None
    expected_sha_source: str = "unknown"
    worker_pool: WorkerPoolInfo = field(default_factory=WorkerPoolInfo)
    analyzer_job: AnalyzerJobInfo = field(default_factory=AnalyzerJobInfo)
    scheduler: SchedulerInfo = field(default_factory=SchedulerInfo)
    collector_status_raw: str | None = None
    collector_status: dict[str, Any] | None = None
    analysis_status_raw: str | None = None
    analysis_status: dict[str, Any] | None = None
    current_json_raw: str | None = None
    current_json: dict[str, Any] | None = None
    generation_files: dict[str, str] = field(default_factory=dict)
    parquet_book: ParquetBookInfo = field(default_factory=ParquetBookInfo)
    executions: list[ExecutionInfo] = field(default_factory=list)
    dashboard_http_status: int | None = None
    dashboard_body: str | None = None
    public_prefix: str = "lighter-mm/public"
    gcs_bucket: str | None = None
    gcs_public_bucket: str | None = None
    analysis_interval_minutes: float = 15.0
    parquet_rotation_minutes: float = 15.0
    gcs_upload_interval_minutes: float = 15.0
    status_ok_minutes: float = 20.0
    status_warn_minutes: float = 40.0


@dataclass
class VerifyReport:
    status: str  # healthy | degraded | unhealthy
    expected_git_sha: str | None
    expected_sha_source: str
    checks: list[CheckResult] = field(default_factory=list)
    timestamps: dict[str, str] = field(default_factory=dict)
    run_ids: dict[str, str] = field(default_factory=dict)
    sections: dict[str, list[CheckResult]] = field(default_factory=dict)

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)
        self.sections.setdefault(check.section, []).append(check)

    def has_fail(self) -> bool:
        return any(c.level == CheckLevel.FAIL for c in self.checks)

    def exit_code(self) -> int:
        return 1 if self.has_fail() else 0

    def finalize_status(self) -> None:
        if self.has_fail():
            self.status = "unhealthy"
        elif any(c.level == CheckLevel.WARN for c in self.checks):
            self.status = "degraded"
        else:
            self.status = "healthy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expected_git_sha": self.expected_git_sha,
            "expected_sha_source": self.expected_sha_source,
            "timestamps": self.timestamps,
            "run_ids": self.run_ids,
            "collector": _section_dict(self, "Collector"),
            "storage": _section_dict(self, "Storage"),
            "analyzer": _section_dict(self, "Analyzer"),
            "public_data": _section_dict(self, "Public data"),
            "dashboard": _section_dict(self, "Dashboard"),
            "deployment": _section_dict(self, "Deployment"),
            "websocket": _section_dict(self, "WebSocket"),
            "checks": [c.to_dict() for c in self.checks],
        }


def _section_dict(report: VerifyReport, section: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in report.sections.get(section, []):
        out[c.name] = f"{c.level.value}: {c.message}"
    return out
