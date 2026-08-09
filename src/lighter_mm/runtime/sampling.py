"""Pure builders for collector sample rows and side-effect payloads."""

from __future__ import annotations

from typing import Any

from lighter_mm.models import MarketStatsSnapshot
from lighter_mm.orderbook.book import LocalOrderBook, OrderBookMetrics


def build_book_sample_row(
    *,
    market_id: int,
    symbol: str,
    metrics: OrderBookMetrics,
    stats: MarketStatsSnapshot | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "timestamp_ms": metrics.timestamp_ms,
        "market_id": market_id,
        "symbol": symbol,
        "best_bid": metrics.best_bid,
        "best_ask": metrics.best_ask,
        "mid": metrics.mid,
        "spread_absolute": metrics.spread_absolute,
        "spread_bps": metrics.spread_bps,
        "best_bid_size_base": metrics.best_bid_size_base,
        "best_ask_size_base": metrics.best_ask_size_base,
        "best_bid_size_usd": metrics.best_bid_size_usd,
        "best_ask_size_usd": metrics.best_ask_size_usd,
        "is_stale": metrics.is_stale,
        "is_usable": metrics.is_usable,
        "is_inactive": metrics.is_inactive,
        "book_update_age_ms": metrics.book_update_age_ms,
        "nonce": metrics.nonce,
        "index_price": float(stats.index_price) if stats and stats.index_price else None,
        "mark_price": float(stats.mark_price) if stats and stats.mark_price else None,
        "stats_mid_price": float(stats.mid_price) if stats and stats.mid_price else None,
        "open_interest": float(stats.open_interest) if stats and stats.open_interest else None,
        "last_trade_price": float(stats.last_trade_price)
        if stats and stats.last_trade_price
        else None,
        "current_funding_rate": float(stats.current_funding_rate)
        if stats and stats.current_funding_rate is not None
        else None,
        "funding_rate": float(stats.funding_rate)
        if stats and stats.funding_rate is not None
        else None,
        "daily_base_token_volume": stats.daily_base_token_volume if stats else None,
        "daily_quote_token_volume": stats.daily_quote_token_volume if stats else None,
        "daily_price_low": stats.daily_price_low if stats else None,
        "daily_price_high": stats.daily_price_high if stats else None,
        "daily_price_change": stats.daily_price_change if stats else None,
    }
    row.update(metrics.depths)
    return row


def build_live_metric(
    *,
    symbol: str,
    metrics: OrderBookMetrics,
    tpm: float,
    markout_5s: float | None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "spread_bps": metrics.spread_bps,
        "depth_10bps": metrics.depths.get("two_sided_depth_10bps_usd"),
        "tpm": tpm,
        "markout_5s": markout_5s,
        "is_stale": metrics.is_inactive,
        "is_usable": metrics.is_usable,
    }


def build_dq_update(
    market_id: int,
    *,
    actual_samples: int,
    book: LocalOrderBook,
) -> tuple[int, dict[str, Any]]:
    return (
        market_id,
        {
            "actual_samples": actual_samples,
            "book_resync_count": book.resync_count,
            "nonce_gap_count": book.nonce_gap_count,
            "stale_book_count": book.stale_count,
        },
    )
