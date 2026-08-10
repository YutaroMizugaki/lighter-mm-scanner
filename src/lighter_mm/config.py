"""Runtime configuration — local and cloud (env-overridable, no secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_first(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v != "":
            return v
    return default


class Settings(BaseSettings):
    """Supports both LIGHTER_MM_* and unprefixed cloud env names."""

    model_config = SettingsConfigDict(
        env_prefix="LIGHTER_MM_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "local"  # local | cloud
    rest_base_url: str = "https://mainnet.zklighter.elliot.ai"
    ws_url: str = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"

    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    tmp_dir: Path = Path("/tmp/lighter-mm")

    book_sample_interval_seconds: float = Field(default=5.0, ge=1.0)
    market_refresh_seconds: int = Field(default=3600, ge=60)
    stale_book_seconds: float = Field(
        default=180.0,
        ge=5.0,
        description="Book inactivity indicator threshold (no WS order-book update); not coverage.",
    )

    # Lighter caps subscriptions per WS connection; keep headroom under 100
    # for market_stats/all and any future channels.
    max_subscriptions_per_connection: int = Field(default=95, ge=1, le=100)
    max_client_messages_per_minute: int = Field(default=150, ge=1, le=200)
    max_inflight_messages: int = Field(default=40, ge=1, le=50)

    ws_ping_interval_seconds: float = 20.0
    ws_reconnect_base_seconds: float = 1.0
    ws_reconnect_max_seconds: float = 60.0

    parquet_flush_rows: int = 500
    parquet_flush_seconds: float = 5.0
    parquet_rotation_minutes: int = Field(default=15, ge=1)
    gcs_upload_interval_minutes: int = Field(default=15, ge=1)
    analysis_interval_minutes: int = Field(default=30, ge=1)
    trade_id_cache_size: int = 50_000

    # Analyzer Cloud Run Job
    analyzer_mount_path: Path = Path("/mnt/lighter-mm")
    analyzer_lock_lease_seconds: int = Field(default=1800, ge=60)
    analyzer_lock_renew_interval_seconds: float = Field(default=60.0, ge=10.0)
    duckdb_memory_limit: str = "1GiB"
    duckdb_threads: int = Field(default=2, ge=1)
    analysis_stale_minutes: float = Field(default=30.0, ge=5.0)
    max_final_analysis_attempts: int = Field(default=3, ge=1)
    sqlite_dq_flush_seconds: float = Field(default=60.0, ge=5.0)
    # Scheduled/final dashboard ranking window. Collector still retains run_target_hours
    # (e.g. 72h). 0 disables rolling and reverts to full-history analysis (legacy).
    scheduled_analysis_window_hours: float = Field(default=24.0, ge=0.0)

    # 0 => continuous; >0 => stop after N hours (cloud deploy sets 72)
    run_target_hours: float = Field(default=0.0)

    gcp_project_id: str | None = None
    gcp_region: str = "asia-northeast1"
    gcs_bucket: str | None = None
    # Optional separate bucket for dashboard JSON (allUsers objectViewer, no raw data).
    gcs_public_bucket: str | None = None
    gcs_prefix: str = "lighter-mm"
    gcs_public_prefix: str = "lighter-mm/public"

    git_sha: str | None = None
    collector_version: str = "0.1.0"
    analyzer_version: str = "0.1.0"

    # Dashboard staleness thresholds (minutes)
    status_ok_minutes: float = 20.0
    status_warn_minutes: float = 40.0
    collector_startup_grace_minutes: float = Field(default=5.0, ge=0.0)

    order_notionals_usd: list[float] = Field(
        default_factory=lambda: [25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]
    )
    depth_bps_levels: list[int] = Field(default_factory=lambda: [5, 10, 25, 50, 100])
    spread_thresholds_bps: list[float] = Field(
        default_factory=lambda: [1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0]
    )
    markout_horizons_seconds: list[int] = Field(default_factory=lambda: [1, 5, 30, 60])

    min_coverage_pct: float = 90.0
    min_trades_per_hour: float = 30.0
    min_two_sided_depth_10bps_usd: float = 200.0
    min_median_spread_bps: float = 1.0
    min_markout_samples_5s: int = 20
    min_markout_samples_30s: int = 20
    min_median_trades_per_minute: float | None = None
    min_observation_hours_for_candidate: float = Field(default=1.0, ge=0.0)

    dashboard_refresh_seconds: float = 2.0
    dashboard_top_n: int = 10
    structured_logging: bool = False

    # Paper Market Maker (historical conservative queue simulation)
    paper_mm_enabled: bool = True
    paper_mm_order_usd: float = Field(default=50.0, ge=1.0)
    paper_mm_max_inventory_usd: float = Field(default=50.0, ge=1.0)
    paper_mm_max_quote_age_seconds: float = Field(default=30.0, ge=1.0)
    paper_mm_top_n: int = Field(default=20, ge=1)
    paper_mm_maker_fee_bps: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _merge_unprefixed_env(cls, data: Any) -> Any:  # noqa: ANN401
        if not isinstance(data, dict):
            data = {}
        mapping = {
            "environment": ("ENVIRONMENT", "LIGHTER_MM_ENVIRONMENT"),
            "rest_base_url": ("LIGHTER_REST_URL", "LIGHTER_MM_REST_BASE_URL"),
            "ws_url": ("LIGHTER_WS_URL", "LIGHTER_MM_WS_URL"),
            "book_sample_interval_seconds": (
                "BOOK_SAMPLE_INTERVAL_SECONDS",
                "LIGHTER_MM_BOOK_SAMPLE_INTERVAL_SECONDS",
            ),
            "parquet_rotation_minutes": (
                "PARQUET_ROTATION_MINUTES",
                "LIGHTER_MM_PARQUET_ROTATION_MINUTES",
            ),
            "gcs_upload_interval_minutes": (
                "GCS_UPLOAD_INTERVAL_MINUTES",
                "LIGHTER_MM_GCS_UPLOAD_INTERVAL_MINUTES",
            ),
            "run_target_hours": ("RUN_TARGET_HOURS", "LIGHTER_MM_RUN_TARGET_HOURS"),
            "gcp_project_id": ("GCP_PROJECT_ID", "LIGHTER_MM_GCP_PROJECT_ID"),
            "gcp_region": ("GCP_REGION", "LIGHTER_MM_GCP_REGION"),
            "gcs_bucket": ("GCS_BUCKET", "LIGHTER_MM_GCS_BUCKET"),
            "gcs_public_bucket": ("GCS_PUBLIC_BUCKET", "LIGHTER_MM_GCS_PUBLIC_BUCKET"),
            "git_sha": ("GIT_SHA", "LIGHTER_MM_GIT_SHA", "COMMIT_SHA"),
            "structured_logging": ("STRUCTURED_LOGGING", "LIGHTER_MM_STRUCTURED_LOGGING"),
            "data_dir": ("DATA_DIR", "LIGHTER_MM_DATA_DIR"),
            "tmp_dir": ("TMP_DIR", "LIGHTER_MM_TMP_DIR"),
            "max_subscriptions_per_connection": (
                "MAX_SUBSCRIPTIONS_PER_CONNECTION",
                "LIGHTER_MM_MAX_SUBSCRIPTIONS_PER_CONNECTION",
            ),
            "analyzer_mount_path": (
                "ANALYZER_MOUNT_PATH",
                "LIGHTER_MM_ANALYZER_MOUNT_PATH",
            ),
            "duckdb_memory_limit": (
                "DUCKDB_MEMORY_LIMIT",
                "LIGHTER_MM_DUCKDB_MEMORY_LIMIT",
            ),
            "duckdb_threads": ("DUCKDB_THREADS", "LIGHTER_MM_DUCKDB_THREADS"),
            "scheduled_analysis_window_hours": (
                "SCHEDULED_ANALYSIS_WINDOW_HOURS",
                "LIGHTER_MM_SCHEDULED_ANALYSIS_WINDOW_HOURS",
            ),
        }
        for field, names in mapping.items():
            if field not in data or data.get(field) in (None, ""):
                val = _env_first(*names)
                if val is not None:
                    data[field] = val
        return data

    @field_validator("structured_logging", mode="before")
    @classmethod
    def _boolish(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower() in {"1", "true", "yes", "on"}
        return v

    @property
    def is_cloud(self) -> bool:
        return self.environment.lower() in {"cloud", "gcp", "production", "prod"}

    @property
    def effective_data_dir(self) -> Path:
        if self.is_cloud:
            return self.tmp_dir
        return self.data_dir

    def hours_or_none(self) -> float | None:
        """None means run forever."""
        if self.run_target_hours <= 0:
            return None
        return float(self.run_target_hours)


def ensure_dirs(settings: Settings) -> None:
    root = settings.effective_data_dir
    root.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    for name in ("book_samples", "trades", "markouts", "aggregates"):
        (root / name).mkdir(parents=True, exist_ok=True)


def build_storage_backend(settings: Settings):
    from lighter_mm.storage.local_backend import LocalStorageBackend

    if settings.is_cloud:
        # Prefer Settings, but also accept raw process env (Cloud Run injects
        # unprefixed GCS_* vars; belt-and-suspenders if pydantic misses one).
        gcs_bucket = settings.gcs_bucket or _env_first("GCS_BUCKET", "LIGHTER_MM_GCS_BUCKET")
        public_bucket = settings.gcs_public_bucket or _env_first(
            "GCS_PUBLIC_BUCKET", "LIGHTER_MM_GCS_PUBLIC_BUCKET"
        )
        if not gcs_bucket:
            raise RuntimeError("GCS_BUCKET is required when ENVIRONMENT=cloud")
        from lighter_mm.storage.gcs_backend import GCSStorageBackend

        local_root = settings.tmp_dir
        local_root.mkdir(parents=True, exist_ok=True)
        return GCSStorageBackend(
            gcs_bucket,
            local_root=local_root,
            project_id=settings.gcp_project_id or _env_first("GCP_PROJECT_ID"),
            make_public_prefix=settings.gcs_public_prefix.rstrip("/") + "/",
            public_bucket_name=public_bucket,
        )
    return LocalStorageBackend(settings.data_dir)
