"""Local price-level order book with nonce continuity checks.

Aligned with official docs + SDK (`lighter.ws_client.WsClient`):
- snapshot on subscribe
- deltas thereafter
- size == 0 deletes level
- begin_nonce must equal previous nonce
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lighter_mm.util import bps_move, safe_mid, utc_ms


@dataclass
class OrderBookMetrics:
    timestamp_ms: int
    market_id: int
    symbol: str
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread_absolute: float | None
    spread_bps: float | None
    best_bid_size_base: float | None
    best_ask_size_base: float | None
    best_bid_size_usd: float | None
    best_ask_size_usd: float | None
    depths: dict[str, float]
    is_stale: bool
    nonce: int | None


@dataclass
class LocalOrderBook:
    market_id: int
    symbol: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    nonce: int | None = None
    begin_nonce: int | None = None
    last_updated_at: int | None = None
    last_message_at_ms: int | None = None
    synced: bool = False
    resync_count: int = 0
    nonce_gap_count: int = 0
    stale_count: int = 0

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.nonce = None
        self.begin_nonce = None
        self.last_updated_at = None
        self.synced = False

    def apply_snapshot(self, order_book: dict[str, Any], recv_ms: int | None = None) -> None:
        self.clear()
        self._replace_side(self.bids, order_book.get("bids") or [])
        self._replace_side(self.asks, order_book.get("asks") or [])
        self.nonce = int(order_book["nonce"]) if order_book.get("nonce") is not None else None
        self.begin_nonce = (
            int(order_book["begin_nonce"]) if order_book.get("begin_nonce") is not None else None
        )
        self.last_updated_at = order_book.get("last_updated_at")
        self.last_message_at_ms = recv_ms or utc_ms()
        self.synced = True

    def apply_delta(self, order_book: dict[str, Any], recv_ms: int | None = None) -> bool:
        """Apply delta. Returns False if nonce gap → caller must resync."""
        begin = order_book.get("begin_nonce")
        if begin is not None and self.nonce is not None and int(begin) != int(self.nonce):
            self.nonce_gap_count += 1
            self.synced = False
            return False

        self._apply_side(self.bids, order_book.get("bids") or [])
        self._apply_side(self.asks, order_book.get("asks") or [])
        if order_book.get("nonce") is not None:
            self.nonce = int(order_book["nonce"])
        if begin is not None:
            self.begin_nonce = int(begin)
        self.last_updated_at = order_book.get("last_updated_at")
        self.last_message_at_ms = recv_ms or utc_ms()
        self.synced = True
        return True

    def mark_resync(self) -> None:
        self.resync_count += 1
        self.clear()

    @staticmethod
    def _replace_side(side: dict[Decimal, Decimal], levels: Iterable[dict[str, Any]]) -> None:
        side.clear()
        for lvl in levels:
            price = Decimal(str(lvl["price"]))
            size = Decimal(str(lvl["size"]))
            if size > 0:
                side[price] = size

    @staticmethod
    def _apply_side(side: dict[Decimal, Decimal], levels: Iterable[dict[str, Any]]) -> None:
        for lvl in levels:
            price = Decimal(str(lvl["price"]))
            size = Decimal(str(lvl["size"]))
            if size == 0:
                side.pop(price, None)
            else:
                side[price] = size

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self.bids:
            return None
        price = max(self.bids)
        return price, self.bids[price]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self.asks:
            return None
        price = min(self.asks)
        return price, self.asks[price]

    def mid(self) -> Decimal | None:
        bb = self.best_bid()
        ba = self.best_ask()
        if bb is None or ba is None:
            return None
        return safe_mid(bb[0], ba[0])

    def is_stale(self, now_ms: int, stale_seconds: float) -> bool:
        """Stale if never synced, or silent far longer than expected.

        Quiet books may not emit 50ms diffs when unchanged, so the threshold
        is intentionally loose. Unsynced books after disconnect are always stale.
        """
        if not self.synced or self.last_message_at_ms is None:
            return True
        return (now_ms - self.last_message_at_ms) > int(stale_seconds * 1000)

    def cumulative_depth_usd(
        self, side: str, mid: Decimal, bps: int
    ) -> Decimal:
        """Quote notional within `bps` of mid on one side."""
        if mid <= 0:
            return Decimal(0)
        band = mid * Decimal(bps) / Decimal(10000)
        total = Decimal(0)
        if side == "bid":
            lo = mid - band
            for price, size in self.bids.items():
                if price >= lo:
                    total += price * size
        else:
            hi = mid + band
            for price, size in self.asks.items():
                if price <= hi:
                    total += price * size
        return total

    def compute_metrics(
        self,
        *,
        depth_bps_levels: list[int],
        stale_seconds: float,
        now_ms: int | None = None,
    ) -> OrderBookMetrics:
        now = now_ms or utc_ms()
        stale = self.is_stale(now, stale_seconds)
        if stale:
            self.stale_count += 1

        bb = self.best_bid()
        ba = self.best_ask()
        best_bid = bb[0] if bb else None
        best_ask = ba[0] if ba else None
        mid = safe_mid(best_bid, best_ask) if bb and ba else None

        spread_abs = None
        spread_bps = None
        if best_bid is not None and best_ask is not None and mid is not None:
            spread_abs = float(best_ask - best_bid)
            spread_bps = bps_move(best_ask - best_bid, mid)

        bid_sz = float(bb[1]) if bb else None
        ask_sz = float(ba[1]) if ba else None
        bid_usd = float(bb[0] * bb[1]) if bb else None
        ask_usd = float(ba[0] * ba[1]) if ba else None

        depths: dict[str, float] = {}
        if mid is not None and not stale and self.synced:
            for bps in depth_bps_levels:
                bid_d = float(self.cumulative_depth_usd("bid", mid, bps))
                ask_d = float(self.cumulative_depth_usd("ask", mid, bps))
                depths[f"bid_depth_{bps}bps_usd"] = bid_d
                depths[f"ask_depth_{bps}bps_usd"] = ask_d
                depths[f"two_sided_depth_{bps}bps_usd"] = min(bid_d, ask_d)
        else:
            for bps in depth_bps_levels:
                depths[f"bid_depth_{bps}bps_usd"] = 0.0
                depths[f"ask_depth_{bps}bps_usd"] = 0.0
                depths[f"two_sided_depth_{bps}bps_usd"] = 0.0

        return OrderBookMetrics(
            timestamp_ms=now,
            market_id=self.market_id,
            symbol=self.symbol,
            best_bid=float(best_bid) if best_bid is not None else None,
            best_ask=float(best_ask) if best_ask is not None else None,
            mid=float(mid) if mid is not None else None,
            spread_absolute=spread_abs,
            spread_bps=None if stale else spread_bps,
            best_bid_size_base=bid_sz,
            best_ask_size_base=ask_sz,
            best_bid_size_usd=bid_usd,
            best_ask_size_usd=ask_usd,
            depths=depths,
            is_stale=stale,
            nonce=self.nonce,
        )
