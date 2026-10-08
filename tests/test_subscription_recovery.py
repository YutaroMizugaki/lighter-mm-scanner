"""Missing subscription recovery without restarting healthy channels or shards."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from lighter_mm.config import Settings
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.ws.manager import ShardPlan, WsManager


def _manager() -> tuple[WsManager, ShardPlan]:
    mgr = WsManager(settings=Settings(), markets={})
    shard = ShardPlan(shard_id=0, market_ids=[1], include_market_stats_all=True)
    mgr._shard_planned = {0: set(shard.channels())}
    mgr._shard_acked = {0: {"order_book/1", "market_stats/all"}}
    return mgr, shard


def test_ack_normalization_dedup_and_unplanned_ack() -> None:
    mgr, _ = _manager()
    mgr._record_subscription_ack(0, "trade:1")
    mgr._record_subscription_ack(0, "trade/1")
    mgr._record_subscription_ack(0, "trade:999")
    assert mgr.runtime.acked_channels == 3
    assert mgr._shard_acked[0] == {"order_book/1", "trade/1", "market_stats/all"}


@pytest.mark.asyncio
async def test_recovery_resends_only_missing_channel_and_preserves_healthy_shard() -> None:
    mgr, shard = _manager()
    mgr._shard_acked[0] = {"order_book/1", "trade/1"}
    mgr._shard_acked[1] = {"order_book/2", "trade/2"}
    ws = AsyncMock()

    async def receive_ack(raw: str) -> None:
        payload = json.loads(raw)
        if payload["type"] == "subscribe":
            mgr._record_subscription_ack(0, "market_stats:all")
            mgr._stop.set()

    ws.send.side_effect = receive_ack
    await mgr._recover_missing_subscriptions(ws, shard, initial_delay=0)
    sent = [json.loads(call.args[0]) for call in ws.send.await_args_list]
    assert sent == [
        {"type": "unsubscribe", "channel": "market_stats/all"},
        {"type": "subscribe", "channel": "market_stats/all"},
    ]
    assert mgr._shard_acked[1] == {"order_book/2", "trade/2"}
    assert mgr.runtime.client_messages_sent == 2


@pytest.mark.asyncio
async def test_quiet_trade_stream_needs_no_retry_or_initial_ack() -> None:
    from lighter_mm.cloud.health import _ws_degraded

    mgr, shard = _manager()
    mgr.runtime.total_shards = mgr.runtime.connected_shards = 1
    mgr.runtime.planned_channels = 3
    for channel in shard.channels():
        mgr._record_subscription_sent(0, channel)
    ws = AsyncMock()
    await mgr._recover_missing_subscriptions(ws, shard, initial_delay=0)
    ws.send.assert_not_awaited()
    assert mgr.runtime.ws_healthy
    assert mgr.runtime.pending_trade_channels == 1
    assert _ws_degraded(mgr.runtime.public_dict()) == []


def test_unsent_channels_and_unconfirmed_books_remain_degraded() -> None:
    from lighter_mm.cloud.health import _ws_degraded

    mgr, shard = _manager()
    mgr.runtime.total_shards = mgr.runtime.connected_shards = 1
    mgr.runtime.planned_channels = 3
    mgr._record_subscription_sent(0, "order_book/1")
    assert not mgr.runtime.ws_healthy
    assert any("send incomplete" in w for w in _ws_degraded(mgr.runtime.public_dict()))
    for channel in shard.channels():
        mgr._record_subscription_sent(0, channel)
    mgr._shard_acked[0].remove("order_book/1")
    mgr._sync_acked_channels()
    assert not mgr.runtime.ws_healthy
    assert any("ACK incomplete" in w for w in _ws_degraded(mgr.runtime.public_dict()))


@pytest.mark.asyncio
async def test_fully_acked_shard_needs_no_retries() -> None:
    mgr, shard = _manager()
    mgr._record_subscription_ack(0, "trade:1")
    ws = AsyncMock()
    await mgr._recover_missing_subscriptions(ws, shard, initial_delay=0)
    ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_collector_does_not_resubscribe() -> None:
    mgr, shard = _manager()
    mgr._stop.set()
    ws = AsyncMock()
    await mgr._recover_missing_subscriptions(ws, shard, initial_delay=0)
    ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_update_confirms_subscription_without_persisting_snapshot() -> None:
    mgr, shard = _manager()
    ws = AsyncMock()
    await mgr._handle_message(ws, shard, {"type": "update/trade", "channel": "trade:1", "trades": []})
    assert "trade/1" in mgr._shard_acked[0]
    assert mgr.runtime.acked_channels == 3


@pytest.mark.asyncio
async def test_subscriber_failure_closes_connection_to_trigger_reconnect() -> None:
    mgr, shard = _manager()
    mgr._subscribe_shard = AsyncMock(side_effect=OSError("send failed"))
    ws = AsyncMock()
    await mgr._subscribe_and_recover(ws, shard)
    ws.close.assert_awaited_once_with(code=1011, reason="subscription recovery failed")


@pytest.mark.asyncio
async def test_missing_book_subscription_invalidates_old_book_before_snapshot() -> None:
    mgr, shard = _manager()
    mgr._shard_acked[0] = {"trade/1", "market_stats/all"}
    book = LocalOrderBook(market_id=1, symbol="M1")
    book.apply_snapshot({"nonce": 1, "bids": [{"price": "1", "size": "1"}], "asks": [{"price": "2", "size": "1"}]})
    mgr.books[1] = book
    ws = AsyncMock()

    async def receive_snapshot(raw: str) -> None:
        if json.loads(raw)["type"] == "subscribe":
            assert not book.synced
            assert not book.bids and not book.asks
            mgr._record_subscription_ack(0, "order_book:1")
            mgr._stop.set()

    ws.send.side_effect = receive_snapshot
    await mgr._recover_missing_subscriptions(ws, shard, initial_delay=0)
    assert mgr.runtime.book_resyncs == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_close_connection_as_failure() -> None:
    import asyncio

    mgr, shard = _manager()
    mgr._subscribe_shard = AsyncMock(side_effect=asyncio.CancelledError())
    ws = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await mgr._subscribe_and_recover(ws, shard)
    ws.close.assert_not_awaited()
