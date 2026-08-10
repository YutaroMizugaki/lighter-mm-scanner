"""Paper MM simulation engine — conservative queue, base-qty orders."""

from __future__ import annotations

import math

from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.paper_mm.markout import (
    drain_pending_paper_markouts,
    register_pending_markout,
    resolve_due_paper_markouts,
)
from lighter_mm.paper_mm.models import (
    BookSnapshot,
    FifoLot,
    PaperFill,
    PaperMmConfig,
    PaperMmState,
    PaperOrder,
    TradeEvent,
)

_FLAT_EPS = 1e-12


def _effective_fee(config: PaperMmConfig) -> tuple[float, bool]:
    if config.maker_fee_bps is None:
        return 0.0, False
    return float(config.maker_fee_bps), True


def _is_flat(position_qty: float) -> bool:
    return abs(position_qty) < _FLAT_EPS


def _position_usd(position_qty: float, price: float) -> float:
    return abs(position_qty) * price


def _entry_order_usd(config: PaperMmConfig) -> float:
    return min(config.order_usd, config.max_inventory_usd)


def _compute_unrealized_pnl(state: PaperMmState, last_mid: float) -> float:
    if last_mid <= 0:
        return 0.0
    unrealized = 0.0
    for lot in state.fifo_lots:
        if lot.side == "long":
            unrealized += (last_mid - lot.price) * lot.qty_base
        else:
            unrealized += (lot.price - last_mid) * lot.qty_base
    return unrealized


def _cancel_order(order: PaperOrder | None) -> None:
    if order is not None and order.status in ("open", "partial"):
        order.status = "cancelled"


def _inventory_usd(state: PaperMmState, price: float) -> float:
    if state.last_mid and state.last_mid > 0:
        price = state.last_mid
    return _position_usd(state.position_qty_base, price)


def _track_inventory_time(state: PaperMmState, ts_ms: int) -> None:
    inv_usd = _inventory_usd(state, 0.0)
    state.max_abs_inventory_usd = max(state.max_abs_inventory_usd, inv_usd)
    if not _is_flat(state.position_qty_base):
        if state.inventory_start_ms is None:
            state.inventory_start_ms = ts_ms
    elif state.inventory_start_ms is not None:
        state.inventory_seconds += (ts_ms - state.inventory_start_ms) / 1000.0
        state.inventory_start_ms = None


def _close_long_lots(
    state: PaperMmState, qty: float, sell_price: float
) -> tuple[float, float]:
    remaining = qty
    realized = 0.0
    spread = 0.0
    while remaining > _FLAT_EPS and state.fifo_lots:
        lot = state.fifo_lots[0]
        if lot.side != "long":
            break
        match = min(remaining, lot.qty_base)
        pnl = (sell_price - lot.price) * match
        realized += pnl
        spread += pnl
        lot.qty_base -= match
        remaining -= match
        if lot.qty_base < _FLAT_EPS:
            state.fifo_lots.pop(0)
    return realized, spread


def _close_short_lots(
    state: PaperMmState, qty: float, buy_price: float
) -> tuple[float, float]:
    remaining = qty
    realized = 0.0
    spread = 0.0
    while remaining > _FLAT_EPS and state.fifo_lots:
        lot = state.fifo_lots[0]
        if lot.side != "short":
            break
        match = min(remaining, lot.qty_base)
        pnl = (lot.price - buy_price) * match
        realized += pnl
        spread += pnl
        lot.qty_base -= match
        remaining -= match
        if lot.qty_base < _FLAT_EPS:
            state.fifo_lots.pop(0)
    return realized, spread


def _apply_fill(
    state: PaperMmState,
    *,
    side: str,
    qty_base: float,
    price: float,
    timestamp_ms: int,
    reference_mid: float,
    fee_bps: float,
    is_partial_order_fill: bool,
) -> None:
    usd = qty_base * price
    fee = usd * fee_bps / 10000.0
    state.fees_usd += fee
    state.filled_notional_usd += usd
    fill = PaperFill(
        side=side,
        qty_base=qty_base,
        price=price,
        usd=usd,
        timestamp_ms=timestamp_ms,
        reference_mid=reference_mid,
        is_partial=is_partial_order_fill,
    )
    state.paper_fills.append(fill)
    register_pending_markout(state, fill)

    if side == "bid":
        state.bid_fills += 1
        short_qty = sum(lot.qty_base for lot in state.fifo_lots if lot.side == "short")
        close_qty = min(qty_base, short_qty)
        if close_qty > _FLAT_EPS:
            pnl, spread = _close_short_lots(state, close_qty, price)
            state.realized_pnl_usd += pnl
            state.gross_spread_capture_usd += spread
        open_qty = qty_base - close_qty
        if open_qty > _FLAT_EPS:
            state.fifo_lots.append(FifoLot(open_qty, price, "long"))
        state.position_qty_base += qty_base
        state.cash_usd -= usd
    else:
        state.ask_fills += 1
        long_qty = sum(lot.qty_base for lot in state.fifo_lots if lot.side == "long")
        close_qty = min(qty_base, long_qty)
        if close_qty > _FLAT_EPS:
            pnl, spread = _close_long_lots(state, close_qty, price)
            state.realized_pnl_usd += pnl
            state.gross_spread_capture_usd += spread
        open_qty = qty_base - close_qty
        if open_qty > _FLAT_EPS:
            state.fifo_lots.append(FifoLot(open_qty, price, "short"))
        state.position_qty_base -= qty_base
        state.cash_usd += usd

    if is_partial_order_fill:
        state.partial_fills += 1
    else:
        state.full_fills += 1

    _track_inventory_time(state, timestamp_ms)
    _update_round_trip(state, timestamp_ms)
    _sync_quotes_after_inventory_fill(state)


def _sync_quotes_after_inventory_fill(state: PaperMmState) -> None:
    """Cancel exit-side quotes so the next book snapshot re-places exact inventory qty."""
    if _is_flat(state.position_qty_base):
        return
    if state.position_qty_base > _FLAT_EPS:
        _cancel_order(state.bid_order)
        state.bid_order = None
        _cancel_order(state.ask_order)
        state.ask_order = None
    else:
        _cancel_order(state.ask_order)
        state.ask_order = None
        _cancel_order(state.bid_order)
        state.bid_order = None


def _update_round_trip(state: PaperMmState, ts_ms: int) -> None:
    flat = _is_flat(state.position_qty_base)
    if state.was_flat and not flat:
        state.entry_fill_ts_ms = ts_ms
        state.was_flat = False
    elif not state.was_flat and flat:
        if state.entry_fill_ts_ms is not None:
            holding = (ts_ms - state.entry_fill_ts_ms) / 1000.0
            state.holding_times_s.append(holding)
            state.round_trips += 1
        state.entry_fill_ts_ms = None
        state.was_flat = True


def _apply_trade_to_order(
    state: PaperMmState,
    config: PaperMmConfig,
    order: PaperOrder,
    trade: TradeEvent,
    fee_bps: float,
    reference_mid: float,
) -> None:
    if order.status not in ("open", "partial"):
        return
    if trade.timestamp_ms <= order.placed_at_ms:
        return

    if order.side == "bid":
        if trade.is_maker_ask:
            return
        if trade.price > order.price:
            return
    else:
        if not trade.is_maker_ask:
            return
        if trade.price < order.price:
            return

    usd_left = trade.usd_amount
    if order.queue_ahead_usd > 0:
        consumed = min(usd_left, order.queue_ahead_usd)
        order.queue_ahead_usd -= consumed
        usd_left -= consumed

    if usd_left <= 0 or order.remaining_qty_base <= _FLAT_EPS:
        return

    max_fill_usd = order.remaining_qty_base * order.price
    fill_usd = min(usd_left, max_fill_usd)
    fill_qty = fill_usd / order.price
    fill_qty = min(fill_qty, order.remaining_qty_base)
    if fill_qty <= _FLAT_EPS:
        return

    is_partial = fill_qty < order.remaining_qty_base - _FLAT_EPS
    _apply_fill(
        state,
        side=order.side,
        qty_base=fill_qty,
        price=order.price,
        timestamp_ms=trade.timestamp_ms,
        reference_mid=reference_mid,
        fee_bps=fee_bps,
        is_partial_order_fill=is_partial,
    )

    order.remaining_qty_base -= fill_qty
    if order.remaining_qty_base <= _FLAT_EPS:
        order.status = "filled"
        order.remaining_qty_base = 0.0
    else:
        order.status = "partial"

    if order.side == "bid" and order.status == "partial":
        _cancel_order(order)
        if state.bid_order is order:
            state.bid_order = None
        _sync_exit_order(state, config)
    elif order.side == "ask" and order.status == "partial":
        _cancel_order(order)
        if state.ask_order is order:
            state.ask_order = None
        _sync_exit_order(state, config)


def _sync_exit_order(state: PaperMmState, config: PaperMmConfig) -> None:
    """After partial same-side fill, cancel same-side remainder and re-place exit on next book."""
    if state.position_qty_base > _FLAT_EPS:
        _cancel_order(state.bid_order)
        state.bid_order = None
        _cancel_order(state.ask_order)
        state.ask_order = None
    elif state.position_qty_base < -_FLAT_EPS:
        _cancel_order(state.ask_order)
        state.ask_order = None
        _cancel_order(state.bid_order)
        state.bid_order = None


def _place_order(
    state: PaperMmState,
    *,
    side: str,
    price: float,
    qty_base: float,
    queue_ahead_usd: float,
    placed_at_ms: int,
) -> PaperOrder:
    order = PaperOrder(
        side=side,
        price=price,
        target_qty_base=qty_base,
        remaining_qty_base=qty_base,
        queue_ahead_usd=queue_ahead_usd,
        placed_at_ms=placed_at_ms,
        last_seen_at_ms=placed_at_ms,
    )
    state.quote_count += 1
    return order


def _should_requote(
    order: PaperOrder | None,
    price: float,
    ts_ms: int,
    max_age_ms: int,
) -> bool:
    if order is None or order.status not in ("open", "partial"):
        return True
    if order.price != price:
        return True
    if ts_ms - order.placed_at_ms > max_age_ms:
        return True
    return False


def _maybe_quote(state: PaperMmState, config: PaperMmConfig, book: BookSnapshot) -> None:
    ts = book.timestamp_ms
    max_age_ms = int(config.max_quote_age_seconds * 1000)
    pos = state.position_qty_base

    if _is_flat(pos):
        entry_usd = _entry_order_usd(config)
        if book.best_bid_size_usd > 0 and book.best_bid > 0:
            if _should_requote(state.bid_order, book.best_bid, ts, max_age_ms):
                _cancel_order(state.bid_order)
                qty = entry_usd / book.best_bid
                state.bid_order = _place_order(
                    state,
                    side="bid",
                    price=book.best_bid,
                    qty_base=qty,
                    queue_ahead_usd=book.best_bid_size_usd,
                    placed_at_ms=ts,
                )
        else:
            _cancel_order(state.bid_order)
            state.bid_order = None

        if book.best_ask_size_usd > 0 and book.best_ask > 0:
            if _should_requote(state.ask_order, book.best_ask, ts, max_age_ms):
                _cancel_order(state.ask_order)
                qty = entry_usd / book.best_ask
                state.ask_order = _place_order(
                    state,
                    side="ask",
                    price=book.best_ask,
                    qty_base=qty,
                    queue_ahead_usd=book.best_ask_size_usd,
                    placed_at_ms=ts,
                )
        else:
            _cancel_order(state.ask_order)
            state.ask_order = None
    elif pos > _FLAT_EPS:
        _cancel_order(state.bid_order)
        state.bid_order = None
        target_qty = abs(pos)
        if book.best_ask_size_usd > 0 and book.best_ask > 0:
            if _should_requote(state.ask_order, book.best_ask, ts, max_age_ms):
                _cancel_order(state.ask_order)
                state.ask_order = _place_order(
                    state,
                    side="ask",
                    price=book.best_ask,
                    qty_base=target_qty,
                    queue_ahead_usd=book.best_ask_size_usd,
                    placed_at_ms=ts,
                )
        else:
            _cancel_order(state.ask_order)
            state.ask_order = None
    else:
        _cancel_order(state.ask_order)
        state.ask_order = None
        target_qty = abs(pos)
        if book.best_bid_size_usd > 0 and book.best_bid > 0:
            if _should_requote(state.bid_order, book.best_bid, ts, max_age_ms):
                _cancel_order(state.bid_order)
                state.bid_order = _place_order(
                    state,
                    side="bid",
                    price=book.best_bid,
                    qty_base=target_qty,
                    queue_ahead_usd=book.best_bid_size_usd,
                    placed_at_ms=ts,
                )
        else:
            _cancel_order(state.bid_order)
            state.bid_order = None


def on_trade(
    state: PaperMmState,
    config: PaperMmConfig,
    trade: TradeEvent,
    mid_hist: MidHistory,
) -> None:
    ref = mid_hist.mid_at_or_before(trade.timestamp_ms)
    if ref is None or ref <= 0:
        ref = state.last_mid or trade.price
    fee_bps, _ = _effective_fee(config)

    if state.bid_order is not None:
        _apply_trade_to_order(state, config, state.bid_order, trade, fee_bps, ref)
        if state.bid_order and state.bid_order.status == "filled":
            state.bid_order = None
    if state.ask_order is not None:
        _apply_trade_to_order(state, config, state.ask_order, trade, fee_bps, ref)
        if state.ask_order and state.ask_order.status == "filled":
            state.ask_order = None


def on_book(
    state: PaperMmState,
    config: PaperMmConfig,
    book: BookSnapshot,
    mid_hist: MidHistory,
) -> None:
    state.samples += 1
    if book.mid > 0:
        mid_hist.add(book.timestamp_ms, book.mid)
        state.last_mid = book.mid
        state.last_mid_ts_ms = book.timestamp_ms

    resolve_due_paper_markouts(state, mid_hist, book.timestamp_ms)

    max_age_ms = int(config.max_quote_age_seconds * 1000)
    if state.bid_order and state.bid_order.status in ("open", "partial"):
        if state.bid_order.price != book.best_bid:
            _cancel_order(state.bid_order)
            state.bid_order = None
        elif book.timestamp_ms - state.bid_order.placed_at_ms > max_age_ms:
            _cancel_order(state.bid_order)
            state.bid_order = None
        else:
            state.bid_order.last_seen_at_ms = book.timestamp_ms

    if state.ask_order and state.ask_order.status in ("open", "partial"):
        if state.ask_order.price != book.best_ask:
            _cancel_order(state.ask_order)
            state.ask_order = None
        elif book.timestamp_ms - state.ask_order.placed_at_ms > max_age_ms:
            _cancel_order(state.ask_order)
            state.ask_order = None
        else:
            state.ask_order.last_seen_at_ms = book.timestamp_ms

    _maybe_quote(state, config, book)


def finalize_state(
    state: PaperMmState,
    config: PaperMmConfig,
    mid_hist: MidHistory,
    end_ms: int,
    window_hours: float,
) -> dict:
    if state.inventory_start_ms is not None:
        state.inventory_seconds += (end_ms - state.inventory_start_ms) / 1000.0
        state.inventory_start_ms = None

    drain_pending_paper_markouts(state, mid_hist, end_ms)

    _, fee_included = _effective_fee(config)
    last_mid = state.last_mid or 0.0
    unrealized = _compute_unrealized_pnl(state, last_mid) if not _is_flat(state.position_qty_base) else 0.0

    gross_pnl = state.realized_pnl_usd + unrealized
    state.gross_trading_pnl_usd = gross_pnl + state.fees_usd
    total_pnl = gross_pnl - state.fees_usd

    window_seconds = max(window_hours * 3600.0, 1.0)
    time_with_inventory_pct = (
        (state.inventory_seconds / window_seconds) * 100.0 if window_seconds > 0 else 0.0
    )

    filled_notional = state.filled_notional_usd
    pnl_bps = (
        (total_pnl / filled_notional * 10000.0) if filled_notional > 0 else None
    )
    pnl_per_hour = total_pnl / window_hours if window_hours > 0 else 0.0

    final_inventory_usd = _inventory_usd(state, 0.0)

    def _median(xs: list[float]) -> float | None:
        if not xs:
            return None
        ys = sorted(xs)
        n = len(ys)
        if n % 2 == 1:
            return ys[n // 2]
        return (ys[n // 2 - 1] + ys[n // 2]) / 2.0

    def _p90(xs: list[float]) -> float | None:
        if not xs:
            return None
        ys = sorted(xs)
        idx = min(len(ys) - 1, math.ceil(0.9 * len(ys)) - 1)
        return ys[idx]

    holding = state.holding_times_s
    return {
        "paper_mm_enabled": True,
        "paper_mm_order_usd": config.order_usd,
        "paper_mm_queue_model": config.queue_model,
        "paper_mm_quote_count": state.quote_count,
        "paper_mm_bid_fills": state.bid_fills,
        "paper_mm_ask_fills": state.ask_fills,
        "paper_mm_partial_fills": state.partial_fills,
        "paper_mm_full_fills": state.full_fills,
        "paper_mm_filled_notional_usd": round(filled_notional, 6),
        "paper_mm_round_trips": state.round_trips,
        "paper_mm_gross_pnl_usd": round(gross_pnl, 6),
        "paper_mm_realized_pnl_usd": round(state.realized_pnl_usd, 6),
        "paper_mm_unrealized_pnl_usd": round(unrealized, 6),
        "paper_mm_fees_usd": round(state.fees_usd, 6),
        "paper_mm_total_pnl_usd": round(total_pnl, 6),
        "paper_mm_pnl_per_hour_usd": round(pnl_per_hour, 6),
        "paper_mm_max_abs_inventory_usd": round(state.max_abs_inventory_usd, 6),
        "paper_mm_time_with_inventory_pct": round(time_with_inventory_pct, 4),
        "paper_mm_median_holding_seconds": _median(holding),
        "paper_mm_p90_holding_seconds": _p90(holding),
        "paper_mm_max_holding_seconds": max(holding) if holding else None,
        "paper_mm_markout_5s_median_bps": _median(state.markout_5s_bps),
        "paper_mm_markout_30s_median_bps": _median(state.markout_30s_bps),
        "paper_mm_markout_5s_count": len(state.markout_5s_bps),
        "paper_mm_markout_30s_count": len(state.markout_30s_bps),
        "paper_mm_final_inventory_usd": round(final_inventory_usd, 6),
        "paper_mm_fee_included": fee_included,
        "paper_mm_pnl_bps_on_filled_notional": round(pnl_bps, 4) if pnl_bps is not None else None,
        "paper_mm_samples": state.samples,
        "paper_mm_status": "ok",
        "paper_mm_gross_spread_capture_usd": round(state.gross_spread_capture_usd, 6),
    }
