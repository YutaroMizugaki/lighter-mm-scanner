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


def _normalize_ws_items(value: object) -> list[dict]:
    """Normalize WS trade / stats batch payloads into a list of dicts."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


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
    planned_channels: int = 0
    acked_channels: int = 0
    subscribed_channels: int = 0  # compat alias for acked_channels
    seen_trade_ids: int = 0
    subscription_errors: int = 0
    trade_parse_errors: int = 0
    last_ws_error: str | None = None

    def public_dict(self) -> dict:
        """Safe subset for public dashboard health (no payloads / secrets)."""
        return {
            "connected_shards": self.connected_shards,
            "total_shards": self.total_shards,
            "planned_channels": self.planned_channels,
            "acked_channels": self.acked_channels,
            "subscribed_channels": self.acked_channels,
            "dropped_connections": self.dropped_connections,
            "subscription_errors": self.subscription_errors,
            "trade_parse_errors": self.trade_parse_errors,
            "book_resyncs": self.book_resyncs,
            "nonce_gaps": self.nonce_gaps,
            "last_ws_error": self.last_ws_error,
        }

    @property
    def ws_healthy(self) -> bool:
        if self.total_shards <= 0:
            return False
        return (
            self.connected_shards == self.total_shards
            and self.acked_channels == self.planned_channels
            and self.planned_channels > 0
        )


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
        self._shard_acked: dict[int, set[str]] = {}
        for mid, meta in self.markets.items():
            self.books.setdefault(mid, LocalOrderBook(market_id=mid, symbol=meta.symbol))

    def plan_shards(self, market_ids: Iterable[int]) -> list[ShardPlan]:
        ids = sorted(set(market_ids))
        # Each market consumes 2 subs; each shard also takes market_stats/all so
        # funding/OI keep updating if shard 0 alone disconnects.
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
                    include_market_stats_all=True,
                )
            )
        if not shards:
            shards.append(ShardPlan(shard_id=0, market_ids=[], include_market_stats_all=True))
        return shards

    async def start(self) -> None:
        self._stop.clear()
        shards = self.plan_shards(self.markets.keys())
        self.runtime.total_shards = len(shards)
        self.runtime.planned_channels = sum(len(s.channels()) for s in shards)
        self.runtime.acked_channels = 0
        self.runtime.subscribed_channels = 0
        self._shard_acked = {s.shard_id: set() for s in shards}
        for shard in shards:
            n_chans = len(shard.channels())
            if n_chans > self.settings.max_subscriptions_per_connection:
                raise RuntimeError(
                    f"shard {shard.shard_id} has {n_chans} channels; "
                    f"exceeds max_subscriptions_per_connection="
                    f"{self.settings.max_subscriptions_per_connection}"
                )
            self._tasks.append(asyncio.create_task(self._run_shard(shard), name=f"ws-{shard.shard_id}"))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Clean cancel does not run the disconnect handler. Invalidate every book
        # so the next start() cannot sample pre-restart BBO/depth as live, and
        # deltas cannot apply onto a stale synced book before a fresh snapshot.
        self.invalidate_all_books()

    def invalidate_all_books(self) -> None:
        for book in self.books.values():
            if book.synced or book.bids or book.asks or book.nonce is not None:
                book.mark_resync()
                self.runtime.book_resyncs += 1

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

    def _sync_acked_channels(self) -> None:
        self.runtime.acked_channels = sum(len(s) for s in self._shard_acked.values())
        self.runtime.subscribed_channels = self.runtime.acked_channels

    def _record_subscription_ack(self, shard_id: int, channel: str) -> None:
        if not channel:
            return
        self._shard_acked.setdefault(shard_id, set()).add(channel)
        self._sync_acked_channels()

    async def _send(self, ws: ClientConnection, payload: dict) -> None:
        await self._msg_bucket.acquire(1)
        await ws.send(json.dumps(payload))
        self.runtime.client_messages_sent += 1

    async def _run_shard(self, shard: ShardPlan) -> None:
        attempt = 0
        # Stagger initial connects so 5 shards do not pull full order-book
        # snapshots at the same instant (1Gi Worker Pool OOM risk on resume).
        if shard.shard_id > 0:
            delay = min(float(shard.shard_id) * 3.0, 15.0)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
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
                    self._shard_acked[shard.shard_id] = set()
                    self._sync_acked_channels()
                    self.runtime.connected_shards = sum(
                        1 for c in self._shard_conns.values() if c is not None
                    )
                    attempt = 0
                    # CRITICAL: subscribe concurrently with the read loop.
                    # Sending all subs before reading causes receive-buffer
                    # backlog (huge order_book snapshots) and server disconnects.
                    sub_task = asyncio.create_task(
                        self._subscribe_shard(ws, shard), name=f"sub-{shard.shard_id}"
                    )
                    resync_task = asyncio.create_task(
                        self._resync_loop(ws, shard), name=f"resync-{shard.shard_id}"
                    )
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
                        sub_task.cancel()
                        resync_task.cancel()
                        await asyncio.gather(sub_task, resync_task, return_exceptions=True)
                        self._shard_conns[shard.shard_id] = None
                        self._shard_acked[shard.shard_id] = set()
                        self._sync_acked_channels()
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
        # Pace subscriptions and yield so the reader can drain snapshots.
        for i, channel in enumerate(shard.channels(), start=1):
            if self._stop.is_set():
                return
            await self._send(ws, {"type": "subscribe", "channel": channel})
            if i % 10 == 0:
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0)

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

        if isinstance(mtype, str) and mtype.startswith("subscribed/"):
            channel = str(msg.get("channel") or "")
            self._record_subscription_ack(shard.shard_id, channel)

        # Explicit Lighter error / failed replies only — unknown types are ignored
        # (schema evolves; do not treat every unfamiliar message as an error).
        if isinstance(mtype, str) and mtype in ("error", "failed"):
            err_text = str(
                msg.get("error")
                or msg.get("message")
                or msg.get("msg")
                or msg
            )
            self.runtime.subscription_errors += 1
            self.runtime.last_ws_error = err_text[:500]
            log.warning(
                "Lighter websocket error (shard=%s): %s", shard.shard_id, err_text
            )
            return

        if mtype in ("subscribed/order_book", "update/order_book"):
            await self._handle_order_book(msg, is_snapshot=(mtype == "subscribed/order_book"))
            return

        if mtype in ("subscribed/trade", "update/trade"):
            # Snapshot trades are used for dedupe warmup only; persist live updates.
            await self._handle_trade(msg, persist=(mtype == "update/trade"))
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
        if is_snapshot:
            book.apply_snapshot(ob, recv_ms=recv)
        elif not book.synced:
            # After disconnect / nonce gap the book is cleared. Deltas are partial
            # level updates — applying them as a snapshot would replace the full
            # book with a hollow one and mark synced=True. Drop until a real
            # subscribed/order_book snapshot arrives.
            return
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

    async def _handle_trade(self, msg: dict, *, persist: bool = True) -> None:
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
        batches: list[dict] = []
        batches.extend(_normalize_ws_items(msg.get("trades")))
        batches.extend(_normalize_ws_items(msg.get("liquidation_trades")))
        for raw in batches:
            try:
                trade = TradeEvent.from_ws(raw)
            except Exception as exc:  # noqa: BLE001
                self.runtime.trade_parse_errors += 1
                if self.runtime.trade_parse_errors <= 5 or (
                    self.runtime.trade_parse_errors % 100 == 0
                ):
                    log.warning(
                        "failed to parse trade payload: %s payload=%r",
                        exc,
                        raw,
                    )
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
            if not persist:
                continue
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
