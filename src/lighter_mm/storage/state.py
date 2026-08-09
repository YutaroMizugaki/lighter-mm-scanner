"""Run state persisted for resume across deploys/restarts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RunState(BaseModel):
    run_id: str
    started_at: str
    ended_at: str | None = None
    status: str = "running"  # running | completed | stopped | error
    observation_target_hours: float | None = None
    last_successful_flush: str | None = None
    last_durable_event_ms: int | None = None
    last_trade_timestamp_ms: int | None = None
    collector_version: str = "0.1.0"
    git_sha: str | None = None
    markets: list[int] = Field(default_factory=list)
    samples_written: int = 0
    trades_written: int = 0
    markouts_written: int = 0
    dropped_connections: int = 0
    book_resyncs: int = 0
    nonce_gaps: int = 0
    deployment_gaps: int = 0
    bytes_uploaded: int = 0
    last_analysis_at: str | None = None
    holder_id: str | None = None
    updated_at: str | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
