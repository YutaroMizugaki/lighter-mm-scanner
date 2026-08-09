"""Shared helpers."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Iterable
from decimal import Decimal


def utc_ms() -> int:
    return int(time.time() * 1000)


def backoff_delay(attempt: int, base: float, maximum: float) -> float:
    exp = min(maximum, base * (2 ** max(0, attempt)))
    jitter = random.uniform(0.0, exp * 0.25)
    return min(maximum, exp + jitter)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def safe_mid(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / Decimal(2)


def bps_move(numer: Decimal, denom: Decimal) -> float | None:
    if denom <= 0:
        return None
    return float((numer / denom) * Decimal(10000))


def dec_to_float(v: Decimal | None) -> float | None:
    if v is None:
        return None
    return float(v)


def mean(xs: Iterable[float]) -> float | None:
    vals = list(xs)
    if not vals:
        return None
    return sum(vals) / len(vals)
