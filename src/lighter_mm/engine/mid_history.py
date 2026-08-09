"""Time-based ring buffer of mid prices for markout."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class MidPoint:
    ts_ms: int
    mid: float


class MidHistory:
    def __init__(
        self,
        *,
        retention_seconds: float = 180.0,
        maxlen: int = 10_000,
    ) -> None:
        self.retention_ms = int(retention_seconds * 1000)
        self._maxlen = maxlen
        self._points: deque[MidPoint] = deque()

    def add(self, ts_ms: int, mid: float) -> None:
        if mid <= 0:
            return
        self._points.append(MidPoint(ts_ms, mid))
        self._prune(ts_ms)

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - self.retention_ms
        while self._points and self._points[0].ts_ms < cutoff:
            self._points.popleft()
        while len(self._points) > self._maxlen:
            self._points.popleft()

    def nearest_at_or_before(self, ts_ms: int) -> MidPoint | None:
        for p in reversed(self._points):
            if p.ts_ms <= ts_ms:
                return p
        return None

    def nearest_at_or_after(self, ts_ms: int) -> MidPoint | None:
        for p in self._points:
            if p.ts_ms >= ts_ms:
                return p
        return None

    def mid_at_or_after(self, ts_ms: int, tolerance_ms: int = 2500) -> float | None:
        pt = self.mid_point_at_or_after(ts_ms, tolerance_ms)
        return pt.mid if pt else None

    def mid_at_or_before(self, ts_ms: int, tolerance_ms: int = 2500) -> float | None:
        pt = self.mid_point_at_or_before(ts_ms, tolerance_ms)
        return pt.mid if pt else None

    def mid_point_at_or_after(self, ts_ms: int, tolerance_ms: int = 2500) -> MidPoint | None:
        for p in self._points:
            if p.ts_ms < ts_ms:
                continue
            if p.ts_ms - ts_ms > tolerance_ms:
                break
            return p
        return None

    def mid_point_at_or_before(self, ts_ms: int, tolerance_ms: int = 2500) -> MidPoint | None:
        for p in reversed(self._points):
            if p.ts_ms > ts_ms:
                continue
            if ts_ms - p.ts_ms > tolerance_ms:
                break
            return p
        return None

    def mid_at(self, ts_ms: int, tolerance_ms: int = 2500) -> float | None:
        """Prefer forward mid within tolerance; fallback to before within tolerance."""
        after = self.mid_point_at_or_after(ts_ms, tolerance_ms)
        if after is not None:
            return after.mid
        before = self.mid_point_at_or_before(ts_ms, tolerance_ms)
        return before.mid if before else None

    def recent_mids(self) -> list[MidPoint]:
        return list(self._points)

    def __len__(self) -> int:
        return len(self._points)
