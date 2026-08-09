"""Pure run lifecycle decisions (no storage I/O)."""

from __future__ import annotations

from datetime import UTC, datetime

from lighter_mm.storage.state import RunState


def remaining_observation_seconds(started_at: str, hours: float | None) -> float | None:
    """Seconds left until observation_target_hours from started_at."""
    if hours is None:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - started).total_seconds()
    except ValueError:
        elapsed = 0.0
    return hours * 3600.0 - elapsed


def build_new_run_state(
    *,
    run_id: str,
    started_at: str,
    observation_target_hours: float | None,
    collector_version: str | None,
    git_sha: str | None,
    holder_id: str,
) -> RunState:
    return RunState(
        run_id=run_id,
        started_at=started_at,
        status="running",
        observation_target_hours=observation_target_hours,
        collector_version=collector_version,
        git_sha=git_sha,
        holder_id=holder_id,
    )


def build_minimal_resumed_state(
    *,
    run_id: str,
    started_at: str,
    observation_target_hours: float | None,
    collector_version: str | None,
    git_sha: str | None,
    holder_id: str,
) -> RunState:
    """Reconstruct minimal state when durable state.json is missing but active_run exists."""
    return RunState(
        run_id=run_id,
        started_at=started_at,
        status="running",
        observation_target_hours=observation_target_hours,
        collector_version=collector_version,
        git_sha=git_sha,
        holder_id=holder_id,
    )
