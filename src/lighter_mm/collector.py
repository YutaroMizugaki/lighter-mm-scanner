"""Main collect loop: discovery → WS → samples → markouts → durable storage."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from lighter_mm.cloud.dashboard_data import build_collector_status_payload
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings, build_storage_backend, ensure_dirs
from lighter_mm.dashboard import LiveDashboard
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.engine.reference_mid import reference_mid_for_trade
from lighter_mm.engine.trade_activity import TradeActivityTracker
from lighter_mm.logging_setup import log_event
from lighter_mm.models import MarketStatsSnapshot, RuntimeCounters, TradeEvent
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.rest.markets import MarketDiscovery
from lighter_mm.storage.lock import LeaderLock, LostLeadershipError, wait_for_leadership
from lighter_mm.storage.parquet_store import ParquetStore
from lighter_mm.storage.sqlite_meta import SqliteMeta
from lighter_mm.storage.state import RunState, now_iso
from lighter_mm.util import utc_ms
from lighter_mm.ws.manager import WsManager

log = logging.getLogger(__name__)


class CollectorApp:
    def __init__(
        self,
        settings: Settings,
        hours: float | None = None,
        resume: bool = True,
    ) -> None:
        self.settings = settings
        # CLI --hours overrides RUN_TARGET_HOURS; None from CLI means use settings.
        # hours<=0 means continuous (matches CLI help / RUN_TARGET_HOURS semantics).
        if hours is None:
            self.hours = settings.hours_or_none()
        elif hours <= 0:
            self.hours = None
        else:
            self.hours = hours
        self.resume = resume
        self.backend = build_storage_backend(settings)
        # Point hot data at backend local dir (tmp in cloud, data/ locally)
        settings.data_dir = self.backend.local_data_dir()
        ensure_dirs(settings)

        self.meta = SqliteMeta(settings.data_dir / "metadata.db")
        self.store = ParquetStore(
            settings.data_dir,
            depth_levels=settings.depth_bps_levels,
            flush_rows=settings.parquet_flush_rows,
            flush_seconds=settings.parquet_flush_seconds,
            rotation_minutes=settings.parquet_rotation_minutes,
        )
        self.discovery = MarketDiscovery(settings)
        self.dashboard = LiveDashboard()
        self.counters = RuntimeCounters(started_at=datetime.now(UTC))
        self.activity = TradeActivityTracker()
        mid_retention = max(self.settings.markout_horizons_seconds) + 120
        self.mid_histories: dict[int, MidHistory] = defaultdict(
            lambda: MidHistory(retention_seconds=mid_retention)
        )
        self.recent_markout_5s: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self.live_metrics: dict[int, dict[str, Any]] = {}
        self._sample_counts: dict[int, int] = defaultdict(int)
        self._stop = asyncio.Event()
        self._ws: WsManager | None = None
        self._completed = False
        self._deployment_gaps = 0
        self._last_trade_ts: int | None = None
        self._last_book_row_written_ts: int | None = None
        self._last_usable_book_sample_ts: int | None = None
        self._trades_without_reference_mid: int = 0
        self.holder_id = uuid.uuid4().hex
        self._lost_leadership = False
        self._pending_dq_rows: list[tuple[int, dict[str, Any]]] = []
        self._last_dq_flush_mono = 0.0

        self.run_id, self.state, resumed = self._resolve_run()
        self.sync = DurableSync(
            self.backend,
            run_id=self.run_id,
            gcs_prefix=settings.gcs_prefix,
            public_prefix=settings.gcs_public_prefix,
        )
        # Preserve cumulative counters across Cloud Run restarts (/tmp is wiped).
        self._samples_baseline = int(self.state.samples_written or 0) if resumed else 0
        self._trades_baseline = int(self.state.trades_written or 0) if resumed else 0
        self._markouts_baseline = int(self.state.markouts_written or 0) if resumed else 0
        self.sync.bytes_uploaded = int(self.state.bytes_uploaded or 0) if resumed else 0
        self._last_trade_ts = (
            int(self.state.last_trade_timestamp_ms)
            if resumed and self.state.last_trade_timestamp_ms is not None
            else None
        )
        # WS runtime counters are process-local; keep durable totals as baselines.
        self._drops_baseline = int(self.state.dropped_connections or 0) if resumed else 0
        self._resyncs_baseline = int(self.state.book_resyncs or 0) if resumed else 0
        self._gaps_baseline = int(self.state.nonce_gaps or 0) if resumed else 0
        self.counters.dropped_connections = self._drops_baseline
        self.counters.book_resyncs = self._resyncs_baseline
        self.counters.nonce_gaps = self._gaps_baseline
        self.lock = LeaderLock(
            self.backend, self.sync.lock_key(), holder_id=self.holder_id, lease_seconds=120
        )
        self.markout = MarkoutEngine(
            horizons=settings.markout_horizons_seconds,
            on_markout=self._on_markout_row,
        )
        self._resumed = resumed
        if resumed:
            self._deployment_gaps = int(self.state.deployment_gaps) + 1
            self.state.deployment_gaps = self._deployment_gaps
            log_event(log, "collector_resumed", f"resumed run {self.run_id}", run_id=self.run_id)

    def _require_leadership(self, *, phase: str) -> None:
        if self._lost_leadership:
            raise LostLeadershipError(f"collector lock lost before {phase}")
        if not self.lock.renew(self.run_id, git_sha=self.settings.git_sha):
            self._lost_leadership = True
            self._stop.set()
            raise LostLeadershipError(f"collector lock lost during {phase}")

    def _resolve_run(self) -> tuple[str, RunState, bool]:
        active = None
        if self.resume:
            active = self.backend.download_json(
                f"{self.settings.gcs_prefix.rstrip('/')}/state/active_run.json"
            )
            if active and active.get("status") == "running" and active.get("run_id"):
                run_id = str(active["run_id"])
                state_key = f"{self.settings.gcs_prefix.rstrip('/')}/runs/{run_id}/state/state.json"
                saved = self.backend.download_json(state_key)
                if saved:
                    state = RunState.model_validate(saved)
                    state.status = "running"
                    state.git_sha = self.settings.git_sha or state.git_sha
                    return run_id, state, True
                # Fall back to local sqlite
                existing = self.meta.get_active_run()
                if existing and existing.get("run_id") == run_id:
                    state = RunState(
                        run_id=run_id,
                        started_at=existing.get("started_at") or now_iso(),
                        status="running",
                        observation_target_hours=self.hours,
                        collector_version=self.settings.collector_version,
                        git_sha=self.settings.git_sha,
                    )
                    return run_id, state, True
                # Fail closed: never mint a new run_id while a running pointer
                # exists — that orphans prior GCS parquet and resets the 72h clock.
                started = active.get("started_at") or active.get("updated_at") or now_iso()
                log.warning(
                    "active_run pointer for %s has no state.json; reconstructing minimal state",
                    run_id,
                )
                state = RunState(
                    run_id=run_id,
                    started_at=str(started),
                    status="running",
                    observation_target_hours=self.hours,
                    collector_version=self.settings.collector_version,
                    git_sha=self.settings.git_sha,
                    holder_id=self.holder_id,
                )
                return run_id, state, True

        run_id = uuid.uuid4().hex[:12]
        state = RunState(
            run_id=run_id,
            started_at=now_iso(),
            status="running",
            observation_target_hours=self.hours,
            collector_version=self.settings.collector_version,
            git_sha=self.settings.git_sha,
            holder_id=self.holder_id,
        )
        return run_id, state, False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._request_stop()))
            except NotImplementedError:
                signal.signal(sig, lambda *_: asyncio.create_task(self._request_stop()))

        # During deploys the previous revision may still hold the lease briefly.
        # Wait instead of exiting immediately (exit → Cloud Run restart storm).
        if not await asyncio.to_thread(
            wait_for_leadership,
            self.lock,
            self.run_id,
            git_sha=self.settings.git_sha,
            timeout_s=180.0,
            poll_s=5.0,
        ):
            log.error("another collector holds the leader lock; exiting")
            raise SystemExit(1)

        # Renew the lease BEFORE hydrate / DuckDB analysis. The previous order
        # ran a full dashboard analyze while the renew loop was not yet started;
        # on 1Gi Worker Pools that OOMs or stalls past the 120s lease and the
        # process is hard-killed before state/latest.json can advance.
        lock_task = asyncio.create_task(self._lock_renew_loop(), name="leader-lock-renew")
        try:
            await asyncio.to_thread(
                self._heartbeat_running, "leader elected; starting collector"
            )

            self.meta.start_run(self.run_id, self.hours)
            markets = await self.discovery.fetch_perp_markets(active_only=True)
            self.discovery.markets = {m.market_id: m for m in markets}
            self.meta.upsert_markets(markets)
            self.counters.markets_total = len(markets)
            self.state.markets = [m.market_id for m in markets]
            log_event(
                log,
                "market_discovered",
                f"discovered {len(markets)} active perp markets",
                run_id=self.run_id,
                detail=str(len(markets)),
            )
            log_event(
                log,
                "collector_started",
                f"collector started env={self.settings.environment}",
                run_id=self.run_id,
                git_sha=self.settings.git_sha,
                collector_version=self.settings.collector_version,
            )

            # Deadline is wall-clock from the original run started_at (not process uptime),
            # so Cloud Run redeploys cannot reset a 72h observation window.
            remaining_s = self._remaining_observation_seconds()
            if self.hours is not None and remaining_s is not None and remaining_s <= 0:
                log.info("observation target already reached; finalizing without analysis")
                self._completed = True
                return  # finally → shutdown

            self._ws = WsManager(
                settings=self.settings,
                markets=dict(self.discovery.markets),
                on_book_update=self._on_book,
                on_trade=self._on_trade,
                on_stats=self._on_stats,
            )
            await self._ws.start()
            log_event(log, "ws_connected", "websocket manager started", run_id=self.run_id)
            await asyncio.to_thread(
                self._heartbeat_running, "websocket manager started; collecting"
            )

            no_dash = os.environ.get("LIGHTER_MM_NO_DASHBOARD", "").lower() in {
                "1",
                "true",
                "yes",
            }
            self._dashboard_enabled = (not no_dash) and self.dashboard.console.is_terminal
            if self._dashboard_enabled:
                self.dashboard.start()
            else:
                log.info("non-TTY / NO_DASHBOARD; Rich live dashboard disabled")

            started = asyncio.get_running_loop().time()
            deadline = (
                None if remaining_s is None else started + max(remaining_s, 0.0)
            )
            # Lightweight pointer/state only — full DuckDB analyze runs in
            # _durable_sync_loop so a resume OOM cannot block lease renewals.
            await self._persist_state(flush_upload=False)

            tasks = [
                self._sample_loop(),
                self._markout_loop(),
                self._flush_loop(),
                self._durable_sync_loop(),
                self._state_heartbeat_loop(),
                self._market_refresh_loop(),
                self._watch_deadline(deadline),
            ]
            if self._dashboard_enabled:
                tasks.append(self._dashboard_loop(started))
            else:
                tasks.append(self._log_progress_loop(started))
            await asyncio.gather(*tasks)
        finally:
            self._stop.set()
            await self.shutdown()
            lock_task.cancel()
            await asyncio.gather(lock_task, return_exceptions=True)

    async def _request_stop(self) -> None:
        log_event(log, "collector_stopping", "shutdown signal received", run_id=self.run_id)
        self._stop.set()

    def _remaining_observation_seconds(self) -> float | None:
        """Seconds left until observation_target_hours from state.started_at."""
        if self.hours is None:
            return None
        try:
            started_at = datetime.fromisoformat(self.state.started_at)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - started_at).total_seconds()
        except ValueError:
            elapsed = 0.0
        return self.hours * 3600.0 - elapsed

    async def _watch_deadline(self, deadline: float | None) -> None:
        if deadline is None:
            await self._stop.wait()
            return
        while not self._stop.is_set():
            # Re-check wall-clock remaining so clock skew / long stalls still finish.
            remaining = self._remaining_observation_seconds()
            if remaining is not None and remaining <= 0:
                log.info("collection hours reached; finalizing")
                self._completed = True
                self._stop.set()
                return
            if asyncio.get_running_loop().time() >= deadline:
                log.info("collection hours reached; finalizing")
                self._completed = True
                self._stop.set()
                return
            await asyncio.sleep(1.0)

    async def shutdown(self) -> None:
        self._stop.set()
        if self._ws:
            await self._ws.stop()
            log_event(log, "ws_disconnected", "websocket stopped", run_id=self.run_id)
        try:
            self.markout.poll(utc_ms(), self.mid_histories)
        except Exception as exc:  # noqa: BLE001
            log.warning("final markout drain failed: %s", exc)
        if self._lost_leadership:
            log.warning("skipping shutdown durable writes — leadership lost")
            return
        try:
            await asyncio.to_thread(self._sync_only, final=True)
            if self._lost_leadership:
                return
            self.store.close()
            if self._completed:
                self.state.status = "completed"
                self.state.ended_at = now_iso()
                self._write_state()
                self._require_leadership(phase="completed state upload")
                self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
                self.backend.upload_json(
                    self.sync.active_pointer_key(),
                    {
                        "run_id": self.run_id,
                        "status": "completed",
                        "updated_at": now_iso(),
                        "git_sha": self.settings.git_sha,
                        "shutdown_reason": "completed",
                    },
                )
                self._create_final_analysis_request()
            else:
                self.state.status = "running"
                self._write_state()
                self._require_leadership(phase="shutdown state upload")
                self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
                self.backend.upload_json(
                    self.sync.active_pointer_key(),
                    {
                        "run_id": self.run_id,
                        "status": "running",
                        "updated_at": now_iso(),
                        "git_sha": self.settings.git_sha,
                        "shutdown_reason": "preempted_or_signal",
                    },
                )
            if not self._lost_leadership:
                self._publish_collector_status()
            self.meta.end_run(self.run_id, status="completed" if self._completed else "stopped")
        except LostLeadershipError:
            log.error("shutdown aborted — lost leadership")
        except Exception as exc:  # noqa: BLE001
            log.exception("shutdown flush failed: %s", exc)
            if not self._lost_leadership:
                self.state.status = "error"
                try:
                    self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self.lock.release()
            await self.discovery.close()
            if getattr(self, "_dashboard_enabled", False):
                self.dashboard.stop()
            self.meta.close()
            log_event(
                log,
                "collector_stopping",
                f"shutdown complete samples={self.store.samples_written} "
                f"trades={self.store.trades_written} markouts={self.store.markouts_written}",
                run_id=self.run_id,
            )

    def _write_state(self) -> None:
        self.state.samples_written = self._samples_baseline + self.store.samples_written
        self.state.trades_written = self._trades_baseline + self.store.trades_written
        self.state.markouts_written = self._markouts_baseline + self.store.markouts_written
        self.state.dropped_connections = self.counters.dropped_connections
        self.state.book_resyncs = self.counters.book_resyncs
        self.state.nonce_gaps = self.counters.nonce_gaps
        self.state.deployment_gaps = self._deployment_gaps
        self.state.last_trade_timestamp_ms = self._last_trade_ts
        self.state.bytes_uploaded = self.sync.bytes_uploaded
        self.state.holder_id = self.holder_id
        self.state.git_sha = self.settings.git_sha
        self.state.touch()

    async def _persist_state(self, *, flush_upload: bool = False) -> None:
        if getattr(self, "_lost_leadership", False):
            return
        self._write_state()
        if flush_upload:
            await asyncio.to_thread(self._sync_only, final=False)
        else:
            self._require_leadership(phase="state heartbeat upload")
            self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
            self.backend.upload_json(
                self.sync.active_pointer_key(),
                {
                    "run_id": self.run_id,
                    "status": self.state.status,
                    "updated_at": now_iso(),
                    "git_sha": self.settings.git_sha,
                },
            )

    def _create_final_analysis_request(self) -> None:
        if getattr(self, "_lost_leadership", False):
            return
        key = self.sync.analysis_request_key(self.run_id)
        existing = self.backend.download_json(key)
        if existing and existing.get("status") in {"done", "pending", "running"}:
            return
        self.backend.upload_json(
            key,
            {
                "run_id": self.run_id,
                "type": "final",
                "status": "pending",
                "requested_at": now_iso(),
                "attempts": 0,
                "last_attempt_at": None,
                "last_error": None,
            },
        )
        log_event(
            log,
            "final_analysis_requested",
            f"final analysis request created for {self.run_id}",
            run_id=self.run_id,
        )

    def _publish_collector_status(self) -> None:
        if getattr(self, "_lost_leadership", False):
            return
        ws_runtime = self._ws.runtime.public_dict() if self._ws is not None else None
        payload = build_collector_status_payload(
            self.state,
            settings=self.settings,
            ws_runtime=ws_runtime,
            last_book_sample_at_ms=self._last_usable_book_sample_ts,
            last_book_row_at_ms=self._last_book_row_written_ts,
            trades_without_reference_mid=self._trades_without_reference_mid,
        )
        self.backend.upload_json(
            self.sync.public_key("collector_status.json"), payload, public=True
        )

    def _sync_only(self, *, final: bool) -> None:
        """Upload closed Parquet parts — no DuckDB analysis."""
        self._require_leadership(phase="parquet sync")
        self.store.maybe_flush()
        self.store.rotate_all()
        closed = self.store.take_closed_paths()
        try:
            uploaded = self.sync.upload_new_parquets(
                self.settings.data_dir, paths=closed, delete_local_on_success=True
            )
        except Exception:
            self.store.requeue_closed_paths(closed)
            raise
        self._require_leadership(phase="post-sync state upload")
        log_event(
            log,
            "gcs_uploaded",
            f"uploaded {len(uploaded)} parquet objects",
            run_id=self.run_id,
            detail=str(len(uploaded)),
        )
        flush_at = now_iso()
        self.state.last_successful_flush = flush_at
        self._write_state()
        self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
        self.backend.upload_json(
            self.sync.active_pointer_key(),
            {
                "run_id": self.run_id,
                "status": self.state.status,
                "updated_at": flush_at,
                "git_sha": self.settings.git_sha,
            },
        )
        self._publish_collector_status()

    def _heartbeat_running(self, note: str) -> None:
        """Advance active_run/state and publish collector_status without analysis."""
        if getattr(self, "_lost_leadership", False):
            return
        self._write_state()
        now = now_iso()
        self._require_leadership(phase="heartbeat upload")
        self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
        self.backend.upload_json(
            self.sync.active_pointer_key(),
            {
                "run_id": self.run_id,
                "status": "running",
                "updated_at": now,
                "git_sha": self.settings.git_sha,
                "note": note,
            },
        )
        try:
            self._publish_collector_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat collector_status publish failed: %s", exc)
        log_event(
            log,
            "collector_heartbeat",
            note,
            run_id=self.run_id,
            git_sha=self.settings.git_sha,
        )

    async def _durable_sync_loop(self) -> None:
        regular = float(self.settings.gcs_upload_interval_minutes * 60)
        delays = [15.0, 60.0, 120.0, 180.0, 300.0, regular]
        step = 0
        while not self._stop.is_set():
            timeout = delays[step] if step < len(delays) else regular
            if step < len(delays):
                step += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
                return
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(self._sync_only, final=False)
            except LostLeadershipError:
                log.error("durable sync stopped — lost leadership")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("durable sync failed: %s", exc)
                try:
                    await asyncio.to_thread(
                        self._heartbeat_running, f"durable sync failed: {exc}"
                    )
                except Exception as hb_exc:  # noqa: BLE001
                    log.warning("sync-failure heartbeat failed: %s", hb_exc)

    async def _state_heartbeat_loop(self) -> None:
        interval = min(60.0, self.settings.analysis_interval_minutes * 60)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                if self._lost_leadership:
                    return
                self._write_state()
                self._require_leadership(phase="state heartbeat")
                self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
            except LostLeadershipError:
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("state heartbeat failed: %s", exc)

    async def _lock_renew_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30.0)
                return
            except TimeoutError:
                pass
            if not self.lock.renew(self.run_id, git_sha=self.settings.git_sha):
                log.error("lost leader lock; stopping collector")
                self._lost_leadership = True
                self._stop.set()
                return

    async def _log_progress_loop(self, started: float) -> None:
        while not self._stop.is_set():
            runtime_s = asyncio.get_running_loop().time() - started
            log.info(
                "progress t=%.0fs markets=%s/%s ws=%s samples=%s trades=%s "
                "resyncs=%s gaps=%s drops=%s",
                runtime_s,
                self.counters.markets_ready,
                self.counters.markets_total,
                self.counters.ws_ok,
                self.counters.samples_written,
                self.store.trades_written,
                self.counters.book_resyncs,
                self.counters.nonce_gaps,
                self.counters.dropped_connections,
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15.0)
            except TimeoutError:
                pass

    async def _on_book(self, market_id: int, book: LocalOrderBook, _kind: str) -> None:
        if not book.synced:
            return
        mid = book.mid()
        if mid is not None:
            ts = book.last_message_at_ms or utc_ms()
            self.mid_histories[market_id].add(ts, float(mid))

    async def _on_trade(self, trade: TradeEvent, symbol: str) -> None:
        hist = self.mid_histories.get(trade.market_id)
        ref = reference_mid_for_trade(hist, trade.timestamp_ms)
        if ref is None:
            self._trades_without_reference_mid += 1
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
        self._last_trade_ts = trade.timestamp_ms
        self.markout.on_trade(trade, symbol, ref)

    async def _on_stats(self, snap: MarketStatsSnapshot) -> None:
        return

    def _on_markout_row(self, row: dict[str, Any]) -> None:
        self.store.write_markout(row)
        self.counters.markouts_written = self.store.markouts_written
        if row.get("horizon_s") == 5 and row.get("maker_markout_bps") is not None:
            self.recent_markout_5s[row["market_id"]].append(float(row["maker_markout_bps"]))

    def _flush_dq_if_due(self, *, force: bool = False) -> None:
        if not self._pending_dq_rows:
            return
        loop = asyncio.get_running_loop()
        due = force or (
            loop.time() - self._last_dq_flush_mono >= self.settings.sqlite_dq_flush_seconds
        )
        if not due:
            return
        rows = self._pending_dq_rows
        self._pending_dq_rows = []
        try:
            self.meta.update_dq_batch(rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("sqlite dq batch write failed: %s", exc)
        self._last_dq_flush_mono = loop.time()

    async def _sample_loop(self) -> None:
        interval = self.settings.book_sample_interval_seconds
        loop = asyncio.get_running_loop()
        next_tick = loop.time() + interval
        self._last_dq_flush_mono = loop.time()

        while not self._stop.is_set():
            delay = max(0.0, next_tick - loop.time())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except TimeoutError:
                pass

            if self._lost_leadership:
                break

            now_mono = loop.time()
            if now_mono - next_tick > interval:
                missed = int((now_mono - next_tick) // interval)
                next_tick += missed * interval

            sample_timestamp_ms = utc_ms()
            if not self._ws:
                next_tick += interval
                continue

            ready = 0
            dq_rows: list[tuple[int, dict[str, Any]]] = []
            for mid, book in self._ws.books.items():
                meta = self.discovery.markets.get(mid)
                symbol = meta.symbol if meta else book.symbol
                metrics = book.compute_metrics(
                    depth_bps_levels=self.settings.depth_bps_levels,
                    stale_seconds=self.settings.stale_book_seconds,
                    now_ms=sample_timestamp_ms,
                )
                if metrics.is_usable:
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
                    "is_usable": metrics.is_usable,
                    "is_inactive": metrics.is_inactive,
                    "book_update_age_ms": metrics.book_update_age_ms,
                    "nonce": metrics.nonce,
                    "index_price": float(stats.index_price) if stats and stats.index_price else None,
                    "mark_price": float(stats.mark_price) if stats and stats.mark_price else None,
                    "stats_mid_price": float(stats.mid_price) if stats and stats.mid_price else None,
                    "open_interest": float(stats.open_interest)
                    if stats and stats.open_interest
                    else None,
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
                self._last_book_row_written_ts = sample_timestamp_ms
                if metrics.is_usable:
                    self._last_usable_book_sample_ts = sample_timestamp_ms
                    self.mid_histories[mid].add(sample_timestamp_ms, metrics.mid)
                tpm = self.activity.trades_per_minute(mid, sample_timestamp_ms)
                m5 = list(self.recent_markout_5s[mid])
                self.live_metrics[mid] = {
                    "symbol": symbol,
                    "spread_bps": metrics.spread_bps,
                    "depth_10bps": metrics.depths.get("two_sided_depth_10bps_usd"),
                    "tpm": tpm,
                    "markout_5s": (sum(m5) / len(m5)) if m5 else None,
                    "is_stale": metrics.is_inactive,
                    "is_usable": metrics.is_usable,
                }
                dq_rows.append(
                    (
                        mid,
                        {
                            "actual_samples": self._sample_counts[mid],
                            "book_resync_count": book.resync_count,
                            "nonce_gap_count": book.nonce_gap_count,
                            "stale_book_count": book.stale_count,
                        },
                    )
                )
            self._pending_dq_rows.extend(dq_rows)
            self._flush_dq_if_due()
            self.counters.markets_ready = ready
            self.counters.samples_written = self.store.samples_written
            if self._ws:
                prev_drops = self.counters.dropped_connections
                self.counters.dropped_connections = (
                    self._drops_baseline + self._ws.runtime.dropped_connections
                )
                if self.counters.dropped_connections > prev_drops:
                    log_event(
                        log,
                        "ws_disconnected",
                        "websocket drop detected",
                        run_id=self.run_id,
                        detail=str(self.counters.dropped_connections),
                    )
                prev_resyncs = self.counters.book_resyncs
                self.counters.book_resyncs = (
                    self._resyncs_baseline + self._ws.runtime.book_resyncs
                )
                if self.counters.book_resyncs > prev_resyncs:
                    log_event(log, "book_resync", "book resync", run_id=self.run_id)
                prev_gaps = self.counters.nonce_gaps
                self.counters.nonce_gaps = self._gaps_baseline + self._ws.runtime.nonce_gaps
                if self.counters.nonce_gaps > prev_gaps:
                    log_event(log, "nonce_gap", "nonce gap", run_id=self.run_id)
                self.counters.client_messages_sent = self._ws.runtime.client_messages_sent
                self.counters.ws_ok = self._ws.runtime.ws_healthy

            next_tick += interval

    async def _markout_loop(self) -> None:
        while not self._stop.is_set():
            self.markout.poll(utc_ms(), self.mid_histories)
            await asyncio.sleep(0.2)

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self.store.maybe_flush()
            # Reap closed minute buckets — live TPM only needs the rolling deque.
            self.activity.pop_closed_minutes(utc_ms())
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
                    self.state.markets = list(self.discovery.markets.keys())
                    if self._ws:
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
            if not m.get("is_usable"):
                continue
            spread = m.get("spread_bps") or 0.0
            depth = m.get("depth_10bps") or 0.0
            tpm = m.get("tpm") or 0.0
            mk = m.get("markout_5s")
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
