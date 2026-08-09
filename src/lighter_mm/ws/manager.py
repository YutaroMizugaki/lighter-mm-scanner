"""Sharded WebSocket manager with rate-limited subscriptions and reconnect."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import ClientConnection

from lighter_mm.config import Settings
from lighter_mm.models import MarketMeta, MarketStatsSnapshot, TradeEvent
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.util import backoff_delay, utc_ms
from lighter_mm.ws.rate_limit import TokenBucket

log = logging.getLogger(__name__)

OnBookUpdate = Callable[[int, LocalOrderBook, str], Awaitable[None] | None]
OnTrade = Callable[[TradeEvent, str], Awaitable[None] | None]
OnStats = Callable[[MarketStatsSnapshot], Awaitable[None] | None]


@dataclass
class ShardPlan:
    shard_id: int
    market_ids: list[int]
    include_market_stats_all: bool = False

    def channels(self) -> list[str]:
        chans: list[str] = []
        for mid in self.market_ids:
            chans.append(f"order_book/{mid}")
            chans.append(f"trade/{mid}")
        if self.include_market_stats_all:
            chans.append("market_stats/all")
        return chans


@dataclass
class WsRuntimeStats:
    dropped_connections: int = 0
    book_resyncs: int = 0
    nonce_gaps: int = 0
    client_messages_sent: int = 0
    connected_shards: int = 0
    total_shards: int = 0
    subscribed_channels: int = 0
    seen_trade_ids: int = 0


@dataclass
class WsManager:
    settings: Settings
    markets: dict[int, MarketMeta]
    books: dict[int, LocalOrderBook] = field(default_factory=dict)
    stats_cache: dict[int, MarketStatsSnapshot] = field(default_factory=dict)
    on_book_update: OnBookUpdate | None = None
    on_trade: OnTrade | None = None
    on_stats: OnStats | None = None
    runtime: WsRuntimeStats = field(default_factory=WsRuntimeStats)

    def __post_init__(self) -> None:
        self._stop = asyncio.Event()
        self._msg_bucket = TokenBucket(self.settings.max_client_messages_per_minute)
        self._trade_ids: deque[int] = deque(maxlen=self.settings.trade_id_cache_size)
        self._trade_id_set: set[int] = set()
        self._resync_queues: dict[int, asyncio.Queue[int]] = {}
        self._tasks: list[asyncio.Task] = []
        self._shard_conns: dict[int, ClientConnection | None] = {}
        for mid, meta in self.markets.items():
            self.books.setdefault(mid, LocalOrderBook(market_id=mid, symbol=meta.symbol))

    def plan_shards(self, market_ids: Iterable[int]) -> list[ShardPlan]:
        ids = sorted(set(market_ids))
        # Each market consumes 2 subs; one shared market_stats/all
        per_conn_markets = max(
            1, (self.settings.max_subscriptions_per_connection - 1) // 2
        )
        shards: list[ShardPlan] = []
        for i in range(0, len(ids), per_conn_markets):
            chunk = ids[i : i + per_conn_markets]
            shards.append(
                ShardPlan(
                    shard_id=len(shards),
                    market_ids=chunk,
                    include_market_stats_all=(len(shards) == 0),
                )
            )
        if not shards:
            shards.append(ShardPlan(shard_id=0, market_ids=[], include_market_stats_all=True))
        return shards

    async def start(self) -> None:
        self._stop.clear()
        shards = self.plan_shards(self.markets.keys())
        self.runtime.total_shards = len(shards)
        for shard in shards:
            self._tasks.append(asyncio.create_task(self._run_shard(shard), name=f"ws-{shard.shard_id}"))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def add_market(self, meta: MarketMeta) -> None:
        self.markets[meta.market_id] = meta
        self.books[meta.market_id] = LocalOrderBook(market_id=meta.market_id, symbol=meta.symbol)
        # Restart sharding on structural change — simple & safe for hourly diffs
        await self.stop()
        await self.start()

    async def remove_market(self, market_id: int) -> None:
        self.markets.pop(market_id, None)
        self.books.pop(market_id, None)
        await self.stop()
        await self.start()

    async def _send(self, ws: ClientConnection, payload: dict) -> None:
        await self._msg_bucket.acquire(1)
        await ws.send(json.dumps(payload))
        self.runtime.client_messages_sent += 1

    async def _run_shard(self, shard: ShardPlan) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.settings.ws_url,
                    ping_interval=self.settings.ws_ping_interval_seconds,
                    ping_timeout=self.settings.ws_ping_interval_seconds * 2,
                    open_timeout=30,
                    max_queue=4096,
                ) as ws:
                    self._shard_conns[shard.shard_id] = ws
                    self.runtime.connected_shards = sum(
                        1 for c in self._shard_conns.values() if c is not None
                    )
                    attempt = 0
                    await self._subscribe_shard(ws, shard)
                    resync_task = asyncio.create_task(self._resync_loop(ws, shard))
                    try:
                        async for raw in ws:
                            if self._stop.is_set():
                                break
                            if isinstance(raw, bytes):
                                raw = raw.decode()
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            await self._handle_message(ws, shard, msg)
                    finally:
                        resync_task.cancel()
                        await asyncio.gather(resync_task, return_exceptions=True)
                        self._shard_conns[shard.shard_id] = None
                        self.runtime.connected_shards = sum(
                            1 for c in self._shard_conns.values() if c is not None
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect is expected
                self.runtime.dropped_connections += 1
                log.warning("WS shard %s disconnected: %s", shard.shard_id, exc)
                # Invalidate books on this shard — must rebuild from snapshot
                for mid in shard.market_ids:
                    book = self.books.get(mid)
                    if book:
                        book.mark_resync()
                        self.runtime.book_resyncs += 1
                delay = backoff_delay(
                    attempt,
                    self.settings.ws_reconnect_base_seconds,
                    self.settings.ws_reconnect_max_seconds,
                )
                attempt += 1
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    continue

    async def _subscribe_shard(self, ws: ClientConnection, shard: ShardPlan) -> None:
        for channel in shard.channels():
            if self._stop.is_set():
                return
            await self._send(ws, {"type": "subscribe", "channel": channel})
            self.runtime.subscribed_channels += 1

    async def _resync_loop(self, ws: ClientConnection, shard: ShardPlan) -> None:
        q = self._resync_queues.setdefault(shard.shard_id, asyncio.Queue())
        while not self._stop.is_set():
            mid = await q.get()
            if mid not in shard.market_ids:
                continue
            book = self.books.get(mid)
            if book:
                book.mark_resync()
                self.runtime.book_resyncs += 1
            # Unsubscribe then subscribe to force a fresh snapshot
            await self._send(ws, {"type": "unsubscribe", "channel": f"order_book/{mid}"})
            await self._send(ws, {"type": "subscribe", "channel": f"order_book/{mid}"})

    def request_resync(self, market_id: int) -> None:
        for shard_id, shard_markets in self._iter_shard_markets():
            if market_id in shard_markets:
                q = self._resync_queues.setdefault(shard_id, asyncio.Queue())
                q.put_nowait(market_id)
                return

    def _iter_shard_markets(self) -> list[tuple[int, list[int]]]:
        shards = self.plan_shards(self.markets.keys())
        return [(s.shard_id, s.market_ids) for s in shards]

    async def _handle_message(
        self, ws: ClientConnection, shard: ShardPlan, msg: dict
    ) -> None:
        mtype = msg.get("type")
        if mtype == "ping":
            await self._send(ws, {"type": "pong"})
            return
        if mtype == "connected":
            return

        if mtype in ("subscribed/order_book", "update/order_book"):
            await self._handle_order_book(msg, is_snapshot=(mtype == "subscribed/order_book"))
            return

        if mtype in ("subscribed/trade", "update/trade"):
            await self._handle_trade(msg)
            return

        if mtype in ("subscribed/market_stats", "update/market_stats"):
            await self._handle_market_stats(msg)
            return

    async def _handle_order_book(self, msg: dict, *, is_snapshot: bool) -> None:
        channel = str(msg.get("channel") or "")
        # channel format: order_book:{MARKET_INDEX}
        try:
            market_id = int(channel.split(":")[1])
        except (IndexError, ValueError):
            return
        book = self.books.get(market_id)
        if book is None:
            meta = self.markets.get(market_id)
            symbol = meta.symbol if meta else str(market_id)
            book = LocalOrderBook(market_id=market_id, symbol=symbol)
            self.books[market_id] = book
        ob = msg.get("order_book") or {}
        recv = int(msg.get("timestamp") or utc_ms())
        if is_snapshot or not book.synced:
            book.apply_snapshot(ob, recv_ms=recv)
        else:
            ok = book.apply_delta(ob, recv_ms=recv)
            if not ok:
                self.runtime.nonce_gaps += 1
                self.request_resync(market_id)
                return
        if self.on_book_update:
            maybe = self.on_book_update(market_id, book, "snapshot" if is_snapshot else "delta")
            if asyncio.iscoroutine(maybe):
                await maybe

    async def _handle_trade(self, msg: dict) -> None:
        channel = str(msg.get("channel") or "")
        try:
            market_id = int(channel.split(":")[1])
        except (IndexError, ValueError):
            market_id = None
        symbol = (
            self.markets[market_id].symbol
            if market_id is not None and market_id in self.markets
            else str(market_id)
        )
        batches = []
        if msg.get("trades"):
            batches.extend(msg["trades"])
        if msg.get("liquidation_trades"):
            batches.extend(msg["liquidation_trades"])
        for raw in batches:
            try:
                trade = TradeEvent.from_ws(raw)
            except Exception:  # noqa: BLE001
                continue
            if trade.trade_id in self._trade_id_set:
                continue
            if len(self._trade_ids) == self._trade_ids.maxlen:
                old = self._trade_ids.popleft()
                self._trade_id_set.discard(old)
            self._trade_ids.append(trade.trade_id)
            self._trade_id_set.add(trade.trade_id)
            self.runtime.seen_trade_ids += 1
            if market_id is not None and trade.market_id != market_id:
                trade.market_id = market_id
            if self.on_trade:
                maybe = self.on_trade(trade, symbol)
                if asyncio.iscoroutine(maybe):
                    await maybe

    async def _handle_market_stats(self, msg: dict) -> None:
        ms = msg.get("market_stats")
        ts = msg.get("timestamp")
        if isinstance(ms, dict) and "market_id" in ms:
            items = [ms]
        elif isinstance(ms, dict):
            items = list(ms.values())
        elif isinstance(ms, list):
            items = ms
        else:
            return
        for raw in items:
            if not isinstance(raw, dict) or "market_id" not in raw:
                continue
            snap = MarketStatsSnapshot.from_ws(raw, updated_at_ms=ts)
            self.stats_cache[snap.market_id] = snap
            if self.on_stats:
                maybe = self.on_stats(snap)
                if asyncio.iscoroutine(maybe):
                    await maybe
