"""Maker markout / adverse-selection measurement."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.models import TradeEvent

log = logging.getLogger(__name__)


@dataclass
class PendingMarkout:
    trade: TradeEvent
    symbol: str
    reference_mid: float
    horizons: list[int]
    remaining: set[int]


class MarkoutEngine:
    """
    Sign convention (maker-positive):
      is_maker_ask True  → sold at ask: (trade_price - future_mid) / ref * 1e4
      is_maker_ask False → bought at bid: (future_mid - trade_price) / ref * 1e4
    """

    def __init__(
        self,
        horizons: list[int],
        on_markout: Callable[[dict], None],
        max_pending: int = 200_000,
    ) -> None:
        self.horizons = sorted(horizons)
        self.on_markout = on_markout
        self._pending: deque[PendingMarkout] = deque()
        self.max_pending = max_pending
        self.dropped_pending = 0

    def on_trade(
        self,
        trade: TradeEvent,
        symbol: str,
        reference_mid: float | None,
    ) -> None:
        if not trade.is_regular:
            return
        if reference_mid is None or reference_mid <= 0:
            return
        if len(self._pending) >= self.max_pending:
            dropped = self._pending.popleft()
            # Visible so adverse-selection undercount is not silent on busy names.
            log.warning(
                "markout pending cap reached; dropped trade_id=%s market=%s remaining=%s",
                dropped.trade.trade_id,
                dropped.trade.market_id,
                sorted(dropped.remaining),
            )
            self.dropped_pending += 1
        self._pending.append(
            PendingMarkout(
                trade=trade,
                symbol=symbol,
                reference_mid=float(reference_mid),
                horizons=list(self.horizons),
                remaining=set(self.horizons),
            )
        )

    def poll(self, now_ms: int, mid_histories: dict[int, MidHistory]) -> int:
        resolved = 0
        keep: deque[PendingMarkout] = deque()
        while self._pending:
            item = self._pending.popleft()
            hist = mid_histories.get(item.trade.market_id)
            if hist is None:
                keep.append(item)
                continue
            done_horizons: list[int] = []
            for h in list(item.remaining):
                target = item.trade.timestamp_ms + h * 1000
                if now_ms < target:
                    continue
                future = hist.mid_at(target, tolerance_ms=2500)
                if future is None:
                    # Give up after long wait past horizon
                    if now_ms > target + 10_000:
                        done_horizons.append(h)
                    continue
                bps = self.compute_markout_bps(
                    trade_price=float(item.trade.price),
                    future_mid=future,
                    reference_mid=item.reference_mid,
                    is_maker_ask=item.trade.is_maker_ask,
                )
                self.on_markout(
                    {
                        "timestamp_ms": item.trade.timestamp_ms,
                        "market_id": item.trade.market_id,
                        "symbol": item.symbol,
                        "trade_id": item.trade.trade_id,
                        "horizon_s": h,
                        "trade_price": float(item.trade.price),
                        "reference_mid": item.reference_mid,
                        "future_mid": future,
                        "maker_markout_bps": bps,
                        "is_maker_ask": item.trade.is_maker_ask,
                    }
                )
                resolved += 1
                done_horizons.append(h)
            for h in done_horizons:
                item.remaining.discard(h)
            if item.remaining:
                keep.append(item)
        self._pending = keep
        return resolved

    @staticmethod
    def compute_markout_bps(
        *,
        trade_price: float,
        future_mid: float,
        reference_mid: float,
        is_maker_ask: bool,
    ) -> float | None:
        if reference_mid <= 0:
            return None
        if is_maker_ask:
            numer = trade_price - future_mid
        else:
            numer = future_mid - trade_price
        return (numer / reference_mid) * 10000.0

    @property
    def pending_count(self) -> int:
        return len(self._pending)
