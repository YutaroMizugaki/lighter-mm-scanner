"""Token-bucket rate limiter for WS client messages."""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_minute: float, capacity: float | None = None) -> None:
        self.rate_per_sec = rate_per_minute / 60.0
        self.capacity = capacity if capacity is not None else float(rate_per_minute)
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.updated_at
        self.updated_at = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)

    async def acquire(self, n: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    return
                need = n - self.tokens
                wait = need / self.rate_per_sec if self.rate_per_sec > 0 else 1.0
            await asyncio.sleep(max(wait, 0.01))
