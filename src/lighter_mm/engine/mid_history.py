"""Ring buffer of mid prices for markout and volatility."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class MidPoint:
    ts_ms: int
    mid: float


class MidHistory:
    def __init__(self, maxlen: int = 20_000) -> None:
        self._points: deque[MidPoint] = deque(maxlen=maxlen)

    def add(self, ts_ms: int, mid: float) -> None:
        if mid <= 0:
            return
        self._points.append(MidPoint(ts_ms, mid))

    def nearest_at_or_before(self, ts_ms: int) -> float | None:
        # Linear scan from right — buffer is time-ordered and short horizons.
        for p in reversed(self._points):
            if p.ts_ms <= ts_ms:
                return p.mid
        return None

    def nearest_at_or_after(self, ts_ms: int) -> float | None:
        for p in self._points:
            if p.ts_ms >= ts_ms:
                return p.mid
        return None

    def mid_at(self, ts_ms: int, tolerance_ms: int = 2500) -> float | None:
        """Closest mid within tolerance (prefer at-or-after for future markouts)."""
        best: MidPoint | None = None
        best_abs = None
        for p in self._points:
            d = abs(p.ts_ms - ts_ms)
            if d <= tolerance_ms and (best_abs is None or d < best_abs):
                best = p
                best_abs = d
        return best.mid if best else None

    def recent_mids(self) -> list[MidPoint]:
        return list(self._points)
