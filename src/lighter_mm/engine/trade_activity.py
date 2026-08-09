"""1-minute trade activity buckets and live TPM estimates."""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field

from lighter_mm.models import TradeEvent, TradeType


@dataclass
class MinuteBucket:
    minute_ms: int
    trade_count: int = 0
    trade_quote_volume_usd: float = 0.0
    trade_base_volume: float = 0.0
    buy_taker_count: int = 0
    sell_taker_count: int = 0
    buy_taker_volume_usd: float = 0.0
    sell_taker_volume_usd: float = 0.0
    sizes_usd: list[float] = field(default_factory=list)
    intertrade_ms: list[float] = field(default_factory=list)
    liquidation_count: int = 0
    liquidation_volume_usd: float = 0.0
    last_trade_ts: int | None = None

    def to_row(self, market_id: int, symbol: str) -> dict:
        return {
            "minute_ms": self.minute_ms,
            "market_id": market_id,
            "symbol": symbol,
            "trade_count": self.trade_count,
            "trade_quote_volume_usd": self.trade_quote_volume_usd,
            "trade_base_volume": self.trade_base_volume,
            "buy_taker_count": self.buy_taker_count,
            "sell_taker_count": self.sell_taker_count,
            "buy_taker_volume_usd": self.buy_taker_volume_usd,
            "sell_taker_volume_usd": self.sell_taker_volume_usd,
            "median_trade_size_usd": (
                statistics.median(self.sizes_usd) if self.sizes_usd else None
            ),
            "mean_trade_size_usd": (
                statistics.fmean(self.sizes_usd) if self.sizes_usd else None
            ),
            "median_intertrade_ms": (
                statistics.median(self.intertrade_ms) if self.intertrade_ms else None
            ),
            "liquidation_count": self.liquidation_count,
            "liquidation_volume_usd": self.liquidation_volume_usd,
        }


class TradeActivityTracker:
    def __init__(self) -> None:
        self._buckets: dict[int, dict[int, MinuteBucket]] = defaultdict(dict)
        self._last_ts: dict[int, int] = {}
        self._recent_tpm: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=60))

    def on_trade(self, trade: TradeEvent) -> MinuteBucket:
        minute = (trade.timestamp_ms // 60_000) * 60_000
        bucket = self._buckets[trade.market_id].get(minute)
        if bucket is None:
            bucket = MinuteBucket(minute_ms=minute)
            self._buckets[trade.market_id][minute] = bucket

        if trade.type == TradeType.LIQUIDATION:
            bucket.liquidation_count += 1
            bucket.liquidation_volume_usd += float(trade.usd_amount)
            return bucket

        if trade.type != TradeType.TRADE:
            # deleverage / market-settlement tracked as liquidation-like specials
            bucket.liquidation_count += 1
            bucket.liquidation_volume_usd += float(trade.usd_amount)
            return bucket

        bucket.trade_count += 1
        usd = float(trade.usd_amount)
        bucket.trade_quote_volume_usd += usd
        bucket.trade_base_volume += float(trade.size)
        bucket.sizes_usd.append(usd)
        if trade.taker_is_buy:
            bucket.buy_taker_count += 1
            bucket.buy_taker_volume_usd += usd
        else:
            bucket.sell_taker_count += 1
            bucket.sell_taker_volume_usd += usd

        prev = self._last_ts.get(trade.market_id)
        if prev is not None and trade.timestamp_ms >= prev:
            bucket.intertrade_ms.append(float(trade.timestamp_ms - prev))
        self._last_ts[trade.market_id] = trade.timestamp_ms
        self._recent_tpm[trade.market_id].append(trade.timestamp_ms)
        return bucket

    def trades_per_minute(self, market_id: int, now_ms: int, window_s: int = 60) -> float:
        cutoff = now_ms - window_s * 1000
        xs = [t for t in self._recent_tpm[market_id] if t >= cutoff]
        return len(xs) * (60.0 / window_s)

    def pop_closed_minutes(self, now_ms: int) -> list[tuple[int, MinuteBucket]]:
        """Return completed minute buckets (older than current minute)."""
        current = (now_ms // 60_000) * 60_000
        out: list[tuple[int, MinuteBucket]] = []
        for mid, minutes in list(self._buckets.items()):
            for minute in list(minutes.keys()):
                if minute < current:
                    out.append((mid, minutes.pop(minute)))
        return out
