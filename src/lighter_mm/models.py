"""Pydantic models aligned with official Lighter REST/WS schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator


class MarketType(StrEnum):
    PERP = "perp"
    SPOT = "spot"


class MarketStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class TradeType(StrEnum):
    TRADE = "trade"
    LIQUIDATION = "liquidation"
    DELEVERAGE = "deleverage"
    MARKET_SETTLEMENT = "market-settlement"


class MarketMeta(BaseModel):
    market_id: int
    symbol: str
    market_type: MarketType = MarketType.PERP
    status: MarketStatus
    maker_fee: Decimal
    taker_fee: Decimal
    min_base_amount: Decimal
    min_quote_amount: Decimal
    supported_price_decimals: int
    supported_size_decimals: int
    supported_quote_decimals: int | None = None
    base_asset_id: int | None = None
    quote_asset_id: int | None = None
    liquidation_fee: Decimal | None = None
    created_at: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MarketMeta:
        return cls(
            market_id=int(raw["market_id"]),
            symbol=str(raw["symbol"]),
            market_type=MarketType(raw.get("market_type", "perp")),
            status=MarketStatus(raw.get("status", "inactive")),
            maker_fee=Decimal(str(raw["maker_fee"])),
            taker_fee=Decimal(str(raw["taker_fee"])),
            min_base_amount=Decimal(str(raw["min_base_amount"])),
            min_quote_amount=Decimal(str(raw["min_quote_amount"])),
            supported_price_decimals=int(raw["supported_price_decimals"]),
            supported_size_decimals=int(raw["supported_size_decimals"]),
            supported_quote_decimals=raw.get("supported_quote_decimals"),
            base_asset_id=raw.get("base_asset_id"),
            quote_asset_id=raw.get("quote_asset_id"),
            liquidation_fee=Decimal(str(raw["liquidation_fee"]))
            if raw.get("liquidation_fee") is not None
            else None,
            created_at=str(raw["created_at"]) if raw.get("created_at") is not None else None,
        )


class BookLevel(BaseModel):
    price: Decimal
    size: Decimal


class MarketStatsSnapshot(BaseModel):
    market_id: int
    symbol: str
    index_price: Decimal | None = None
    mark_price: Decimal | None = None
    mid_price: Decimal | None = None
    best_bid_price: Decimal | None = None
    best_ask_price: Decimal | None = None
    open_interest: Decimal | None = None
    last_trade_price: Decimal | None = None
    current_funding_rate: Decimal | None = None
    funding_rate: Decimal | None = None
    funding_timestamp: int | None = None
    daily_base_token_volume: float | None = None
    daily_quote_token_volume: float | None = None
    daily_price_low: float | None = None
    daily_price_high: float | None = None
    daily_price_change: float | None = None
    updated_at_ms: int | None = None

    @staticmethod
    def _dec(v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return Decimal(str(v))

    @classmethod
    def from_ws(cls, raw: dict[str, Any], updated_at_ms: int | None = None) -> MarketStatsSnapshot:
        return cls(
            market_id=int(raw["market_id"]),
            symbol=str(raw["symbol"]),
            index_price=cls._dec(raw.get("index_price")),
            mark_price=cls._dec(raw.get("mark_price")),
            mid_price=cls._dec(raw.get("mid_price")),
            best_bid_price=cls._dec(raw.get("best_bid_price")),
            best_ask_price=cls._dec(raw.get("best_ask_price")),
            open_interest=cls._dec(raw.get("open_interest")),
            last_trade_price=cls._dec(raw.get("last_trade_price")),
            current_funding_rate=cls._dec(raw.get("current_funding_rate")),
            funding_rate=cls._dec(raw.get("funding_rate")),
            funding_timestamp=raw.get("funding_timestamp"),
            daily_base_token_volume=_float_or_none(raw.get("daily_base_token_volume")),
            daily_quote_token_volume=_float_or_none(raw.get("daily_quote_token_volume")),
            daily_price_low=_float_or_none(raw.get("daily_price_low")),
            daily_price_high=_float_or_none(raw.get("daily_price_high")),
            daily_price_change=_float_or_none(raw.get("daily_price_change")),
            updated_at_ms=updated_at_ms,
        )


def _float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


class TradeEvent(BaseModel):
    trade_id: int
    timestamp_ms: int
    market_id: int
    price: Decimal
    size: Decimal
    usd_amount: Decimal
    is_maker_ask: bool
    type: TradeType = TradeType.TRADE
    tx_hash: str | None = None
    block_height: int | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> Any:
        if v is None or v == "":
            return TradeType.TRADE
        return v

    @classmethod
    def from_ws(cls, raw: dict[str, Any]) -> TradeEvent:
        return cls(
            trade_id=int(raw["trade_id"]),
            timestamp_ms=int(raw["timestamp"]),
            market_id=int(raw["market_id"]),
            price=Decimal(str(raw["price"])),
            size=Decimal(str(raw["size"])),
            usd_amount=Decimal(str(raw.get("usd_amount") or 0)),
            is_maker_ask=bool(raw["is_maker_ask"]),
            type=TradeType(raw.get("type") or "trade"),
            tx_hash=raw.get("tx_hash"),
            block_height=raw.get("block_height"),
        )

    @property
    def is_regular(self) -> bool:
        return self.type == TradeType.TRADE

    @property
    def taker_is_buy(self) -> bool:
        # If maker is on ask, taker bought (lifted ask).
        return bool(self.is_maker_ask)


class RuntimeCounters(BaseModel):
    started_at: datetime | None = None
    markets_total: int = 0
    markets_ready: int = 0
    ws_ok: bool = False
    dropped_connections: int = 0
    book_resyncs: int = 0
    nonce_gaps: int = 0
    samples_written: int = 0
    trades_written: int = 0
    markouts_written: int = 0
    client_messages_sent: int = 0
