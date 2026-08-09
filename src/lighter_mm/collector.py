"""Main collect loop: discovery → WS → samples → markouts → storage."""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from lighter_mm.config import Settings, ensure_dirs
from lighter_mm.dashboard import LiveDashboard
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.engine.trade_activity import TradeActivityTracker
from lighter_mm.models import MarketStatsSnapshot, RuntimeCounters, TradeEvent
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.rest.markets import MarketDiscovery
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.sqlite_meta import SqliteMeta
from lighter_mm.util import utc_ms
from lighter_mm.ws.manager import WsManager

log = logging.getLogger(__name__)


class CollectorApp:
    def __init__(self, settings: Settings, hours: float | None = None, resume: bool = True) -> None:
        self.settings = settings
        self.hours = hours
        self.resume = resume
        ensure_dirs(settings)
        self.meta = SqliteMeta(settings.data_dir / "metadata.db")
        self.store = ParquetStore(
            settings.data_dir,
            depth_levels=settings.depth_bps_levels,
            flush_rows=settings.parquet_flush_rows,
            flush_seconds=settings.parquet_flush_seconds,
        )
        self.discovery = MarketDiscovery(settings)
        self.dashboard = LiveDashboard()
        self.counters = RuntimeCounters(started_at=datetime.now(UTC))
        self.activity = TradeActivityTracker()
        self.mid_histories: dict[int, MidHistory] = defaultdict(MidHistory)
        self.recent_markout_5s: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self.live_metrics: dict[int, dict[str, Any]] = {}
        self._sample_counts: dict[int, int] = defaultdict(int)
        self._stop = asyncio.Event()
        self._ws: WsManager | None = None
        self.run_id = self._resolve_run_id()
        self.markout = MarkoutEngine(
            horizons=settings.markout_horizons_seconds,
            on_markout=self._on_markout_row,
        )

    def _resolve_run_id(self) -> str:
        if self.resume:
            existing = self.meta.get_active_run()
            if existing and existing.get("status") == "running":
                return str(existing["run_id"])
        return uuid.uuid4().hex[:12]

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._request_stop()))
            except NotImplementedError:
                signal.signal(sig, lambda *_: asyncio.create_task(self._request_stop()))

        self.meta.start_run(self.run_id, self.hours)
        markets = await self.discovery.fetch_perp_markets(active_only=True)
        self.discovery.markets = {m.market_id: m for m in markets}
        self.meta.upsert_markets(markets)
        self.counters.markets_total = len(markets)
        log.info("discovered %d active perp markets", len(markets))

        self._ws = WsManager(
            settings=self.settings,
            markets=dict(self.discovery.markets),
            on_book_update=self._on_book,
            on_trade=self._on_trade,
            on_stats=self._on_stats,
        )
        await self._ws.start()
        self.dashboard.start()

        started = asyncio.get_running_loop().time()
        deadline = None if self.hours is None else started + self.hours * 3600.0

        try:
            await asyncio.gather(
                self._sample_loop(),
                self._markout_loop(),
                self._flush_loop(),
                self._market_refresh_loop(),
                self._dashboard_loop(started),
                self._watch_deadline(deadline),
            )
        finally:
            await self.shutdown()

    async def _request_stop(self) -> None:
        log.info("shutdown signal received")
        self._stop.set()

    async def _watch_deadline(self, deadline: float | None) -> None:
        if deadline is None:
            await self._stop.wait()
            return
        while not self._stop.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                log.info("collection hours reached; stopping")
                self._stop.set()
                return
            await asyncio.sleep(1.0)

    async def shutdown(self) -> None:
        self._stop.set()
        if self._ws:
            await self._ws.stop()
        self.store.close()
        self.meta.set_kv(
            "counters",
            self.counters.model_dump_json(),
        )
        self.meta.end_run(self.run_id, status="stopped")
        await self.discovery.close()
        self.dashboard.stop()
        self.meta.close()
        log.info(
            "shutdown complete samples=%s trades=%s markouts=%s",
            self.store.samples_written,
            self.store.trades_written,
            self.store.markouts_written,
        )

    async def _on_book(self, market_id: int, book: LocalOrderBook, _kind: str) -> None:
        mid = book.mid()
        if mid is not None:
            self.mid_histories[market_id].add(utc_ms(), float(mid))

    async def _on_trade(self, trade: TradeEvent, symbol: str) -> None:
        hist = self.mid_histories.get(trade.market_id)
        ref = None
        if hist is not None:
            ref = hist.nearest_at_or_before(trade.timestamp_ms)
            if ref is None:
                ref = hist.mid_at(trade.timestamp_ms, tolerance_ms=2000)
        self.activity.on_trade(trade)
        self.store.write_trade(
            {
                "timestamp_ms": trade.timestamp_ms,
                "market_id": trade.market_id,
                "symbol": symbol,
                "trade_id": trade.trade_id,
                "price": float(trade.price),
                "size": float(trade.size),
                "usd_amount": float(trade.usd_amount),
                "is_maker_ask": trade.is_maker_ask,
                "taker_is_buy": trade.taker_is_buy,
                "type": trade.type.value,
                "reference_mid": ref,
            }
        )
        self.counters.trades_written = self.store.trades_written
        self.markout.on_trade(trade, symbol, ref)

    async def _on_stats(self, snap: MarketStatsSnapshot) -> None:
        return

    def _on_markout_row(self, row: dict[str, Any]) -> None:
        self.store.write_markout(row)
        self.counters.markouts_written = self.store.markouts_written
        if row.get("horizon_s") == 5 and row.get("maker_markout_bps") is not None:
            self.recent_markout_5s[row["market_id"]].append(float(row["maker_markout_bps"]))

    async def _sample_loop(self) -> None:
        interval = self.settings.book_sample_interval_seconds
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            if not self._ws:
                continue
            now = utc_ms()
            ready = 0
            for mid, book in self._ws.books.items():
                meta = self.discovery.markets.get(mid)
                symbol = meta.symbol if meta else book.symbol
                metrics = book.compute_metrics(
                    depth_bps_levels=self.settings.depth_bps_levels,
                    stale_seconds=self.settings.stale_book_seconds,
                    now_ms=now,
                )
                if book.synced and not metrics.is_stale:
                    ready += 1
                stats = self._ws.stats_cache.get(mid)
                row: dict[str, Any] = {
                    "timestamp_ms": metrics.timestamp_ms,
                    "market_id": mid,
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
                self.store.write_book(row)
                self._sample_counts[mid] += 1
                if metrics.mid is not None:
                    self.mid_histories[mid].add(now, metrics.mid)
                tpm = self.activity.trades_per_minute(mid, now)
                m5 = list(self.recent_markout_5s[mid])
                self.live_metrics[mid] = {
                    "symbol": symbol,
                    "spread_bps": metrics.spread_bps,
                    "depth_10bps": metrics.depths.get("two_sided_depth_10bps_usd"),
                    "tpm": tpm,
                    "markout_5s": (sum(m5) / len(m5)) if m5 else None,
                    "is_stale": metrics.is_stale,
                }
                self.meta.update_dq(
                    mid,
                    actual_samples=self._sample_counts[mid],
                    book_resync_count=book.resync_count,
                    nonce_gap_count=book.nonce_gap_count,
                    stale_book_count=book.stale_count,
                )
            self.counters.markets_ready = ready
            self.counters.samples_written = self.store.samples_written
            if self._ws:
                self.counters.dropped_connections = self._ws.runtime.dropped_connections
                self.counters.book_resyncs = self._ws.runtime.book_resyncs
                self.counters.nonce_gaps = self._ws.runtime.nonce_gaps
                self.counters.ws_ok = self._ws.runtime.connected_shards > 0

    async def _markout_loop(self) -> None:
        while not self._stop.is_set():
            self.markout.poll(utc_ms(), self.mid_histories)
            await asyncio.sleep(0.2)

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self.store.maybe_flush()
            self.meta.set_kv("counters", self.counters.model_dump_json())
            await asyncio.sleep(1.0)

    async def _market_refresh_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.market_refresh_seconds
                )
                return
            except TimeoutError:
                pass
            try:
                added, removed = await self.discovery.refresh()
                if added or removed:
                    self.meta.upsert_markets(list(self.discovery.markets.values()))
                    self.counters.markets_total = len(self.discovery.markets)
                    if self._ws:
                        # Rebuild shard plan once for the full market set
                        self._ws.markets = dict(self.discovery.markets)
                        for m in added:
                            self._ws.books[m.market_id] = LocalOrderBook(
                                market_id=m.market_id, symbol=m.symbol
                            )
                        for m in removed:
                            self._ws.books.pop(m.market_id, None)
                        await self._ws.stop()
                        await self._ws.start()
            except Exception as exc:  # noqa: BLE001
                log.warning("market refresh failed: %s", exc)

    async def _dashboard_loop(self, started: float) -> None:
        while not self._stop.is_set():
            runtime_s = asyncio.get_running_loop().time() - started
            top = self._live_top(self.settings.dashboard_top_n)
            self.dashboard.update(
                runtime_s=runtime_s,
                markets_ready=self.counters.markets_ready,
                markets_total=self.counters.markets_total,
                ws_ok=self.counters.ws_ok,
                dropped_connections=self.counters.dropped_connections,
                book_resyncs=self.counters.book_resyncs,
                nonce_gaps=self.counters.nonce_gaps,
                samples_written=self.counters.samples_written,
                trades=self.store.trades_written,
                markouts=self.store.markouts_written,
                top=top,
            )
            await asyncio.sleep(self.settings.dashboard_refresh_seconds)

    def _live_top(self, n: int) -> list[dict[str, Any]]:
        rows = []
        for mid, m in self.live_metrics.items():
            if m.get("is_stale"):
                continue
            spread = m.get("spread_bps") or 0.0
            depth = m.get("depth_10bps") or 0.0
            tpm = m.get("tpm") or 0.0
            mk = m.get("markout_5s")
            # Crude live score — NOT the historical MM Opportunity Score
            score = (
                min(spread, 50.0) * 0.4
                + min(depth / 1000.0, 30.0)
                + min(tpm, 40.0) * 0.5
                + (max(mk, -10.0) if mk is not None else -5.0)
            )
            rows.append({**m, "market_id": mid, "live_score": score})
        rows.sort(key=lambda r: r["live_score"], reverse=True)
        return rows[:n]


async def run_collector(settings: Settings, hours: float | None = None) -> None:
    app = CollectorApp(settings, hours=hours)
    await app.run()
