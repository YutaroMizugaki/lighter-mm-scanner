"""Paper MM data models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

PaperOrderSide = Literal["bid", "ask"]
PaperOrderStatus = Literal["open", "partial", "filled", "cancelled"]


@dataclass
class PaperOrder:
    side: PaperOrderSide
    price: float
    target_qty_base: float
    remaining_qty_base: float
    queue_ahead_usd: float
    placed_at_ms: int
    last_seen_at_ms: int
    status: PaperOrderStatus = "open"


@dataclass
class FifoLot:
    qty_base: float
    price: float
    side: Literal["long", "short"]


@dataclass
class PaperFill:
    side: PaperOrderSide
    qty_base: float
    price: float
    usd: float
    timestamp_ms: int
    reference_mid: float
    is_partial: bool
    markout_5s_resolved: bool = False
    markout_30s_resolved: bool = False


@dataclass
class PendingPaperMarkout:
    fill: PaperFill
    remaining_horizons_ms: set[int]


@dataclass
class BookSnapshot:
    timestamp_ms: int
    best_bid: float
    best_ask: float
    best_bid_size_usd: float
    best_ask_size_usd: float
    mid: float


@dataclass
class TradeEvent:
    timestamp_ms: int
    trade_id: int
    price: float
    usd_amount: float
    is_maker_ask: bool


@dataclass
class PaperMmConfig:
    order_usd: float = 50.0
    max_inventory_usd: float = 50.0
    max_quote_age_seconds: float = 30.0
    maker_fee_bps: float | None = None
    queue_model: str = "conservative_touch_ahead"


@dataclass
class PaperMmState:
    position_qty_base: float = 0.0
    cash_usd: float = 0.0
    bid_order: PaperOrder | None = None
    ask_order: PaperOrder | None = None
    fifo_lots: list[FifoLot] = field(default_factory=list)

    bid_fills: int = 0
    ask_fills: int = 0
    partial_fills: int = 0
    full_fills: int = 0
    filled_notional_usd: float = 0.0
    gross_spread_capture_usd: float = 0.0
    fees_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    gross_trading_pnl_usd: float = 0.0
    round_trips: int = 0
    holding_times_s: list[float] = field(default_factory=list)
    max_abs_inventory_usd: float = 0.0
    inventory_seconds: float = 0.0
    quote_count: int = 0
    samples: int = 0

    was_flat: bool = True
    entry_fill_ts_ms: int | None = None
    inventory_start_ms: int | None = None
    paper_fills: list[PaperFill] = field(default_factory=list)
    markout_5s_bps: list[float] = field(default_factory=list)
    markout_30s_bps: list[float] = field(default_factory=list)
    pending_markouts: deque[PendingPaperMarkout] = field(default_factory=deque)

    last_mid: float | None = None
    last_mid_ts_ms: int | None = None
