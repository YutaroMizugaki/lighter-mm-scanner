"""Storage growth estimation helpers."""

from __future__ import annotations

from typing import Any


def estimate_storage(*, bytes_so_far: int, elapsed_hours: float) -> dict[str, Any]:
    elapsed_hours = max(elapsed_hours, 1 / 60)
    bytes_per_hour = bytes_so_far / elapsed_hours
    mb_per_hour = bytes_per_hour / (1024**2)

    def proj(hours: float) -> dict[str, float]:
        b = bytes_per_hour * hours
        return {"hours": hours, "bytes": b, "mb": b / (1024**2), "gb": b / (1024**3)}

    return {
        "bytes_so_far": bytes_so_far,
        "elapsed_hours": elapsed_hours,
        "mb_per_hour": mb_per_hour,
        "estimate_24h": proj(24),
        "estimate_72h": proj(72),
        "estimate_30d": proj(24 * 30),
    }
