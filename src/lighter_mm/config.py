"""Runtime configuration with safe rate-limit defaults."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LIGHTER_MM_",
        env_file=".env",
        extra="ignore",
    )

    # Official mainnet endpoints (https://apidocs.lighter.xyz/)
    rest_base_url: str = "https://mainnet.zklighter.elliot.ai"
    ws_url: str = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"

    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")

    book_sample_interval_seconds: float = Field(default=5.0, ge=1.0)
    market_refresh_seconds: int = Field(default=3600, ge=60)
    # Quiet books may not emit diffs; keep this loose (connection loss → unsynced).
    stale_book_seconds: float = Field(default=180.0, ge=5.0)

    # Official WS: 500 subs/conn, 200 client msgs/min — use margin
    max_subscriptions_per_connection: int = Field(default=450, ge=1, le=500)
    max_client_messages_per_minute: int = Field(default=150, ge=1, le=200)
    max_inflight_messages: int = Field(default=40, ge=1, le=50)

    ws_ping_interval_seconds: float = 20.0
    ws_reconnect_base_seconds: float = 1.0
    ws_reconnect_max_seconds: float = 60.0

    parquet_flush_rows: int = 500
    parquet_flush_seconds: float = 5.0
    trade_id_cache_size: int = 50_000

    # Analysis / scoring defaults
    order_notionals_usd: list[float] = Field(
        default_factory=lambda: [25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]
    )
    depth_bps_levels: list[int] = Field(default_factory=lambda: [5, 10, 25, 50, 100])
    spread_thresholds_bps: list[float] = Field(
        default_factory=lambda: [1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0]
    )
    markout_horizons_seconds: list[int] = Field(default_factory=lambda: [1, 5, 30, 60])

    # Candidate filter soft floors (also use percentiles in scoring)
    min_coverage_pct: float = 90.0
    min_trades_per_hour: float = 30.0
    min_two_sided_depth_10bps_usd: float = 200.0
    min_median_spread_bps: float = 1.0

    dashboard_refresh_seconds: float = 2.0
    dashboard_top_n: int = 10


def ensure_dirs(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    for name in ("book_samples", "trades", "markouts", "aggregates"):
        (settings.data_dir / name).mkdir(parents=True, exist_ok=True)
