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

from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings, build_storage_backend, ensure_dirs
from lighter_mm.dashboard import LiveDashboard
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.engine.trade_activity import TradeActivityTracker
from lighter_mm.logging_setup import log_event
from lighter_mm.models import MarketStatsSnapshot, RuntimeCounters, TradeEvent
from lighter_mm.orderbook.book import LocalOrderBook
from lighter_mm.rest.markets import MarketDiscovery
from lighter_mm.storage.lock import LeaderLock, wait_for_leadership
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

            if self._resumed:
                def _renew_during_hydrate(scanned: int, restored: int) -> None:
                    self.lock.renew(self.run_id, git_sha=self.settings.git_sha)
                    log.info(
                        "hydrate progress scanned=%s restored=%s",
                        scanned,
                        restored,
                        extra={"event": "parquet_hydrate_progress"},
                    )

                restored = await asyncio.to_thread(
                    self.sync.hydrate_run_parquets,
                    self.settings.data_dir,
                    on_progress=_renew_during_hydrate,
                    progress_every=20,
                )
                log_event(
                    log,
                    "parquet_hydrated",
                    f"hydrated {len(restored)} parquet objects from durable storage",
                    run_id=self.run_id,
                    detail=str(len(restored)),
                )
                await asyncio.to_thread(
                    self._heartbeat_running, f"hydrated {len(restored)} parquet objects"
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
                log.info("observation target already reached; writing final dashboard")
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
                self._analysis_loop(),
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
            lock_task.cancel()
            await asyncio.gather(lock_task, return_exceptions=True)
            await self.shutdown()

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
        # Renew lease through the final flush so a waiting revision cannot steal
        # leadership mid-upload (lease is 120s; final sync can exceed that).
        self.lock.renew(self.run_id, git_sha=self.settings.git_sha)
        if self._ws:
            await self._ws.stop()
            log_event(log, "ws_disconnected", "websocket stopped", run_id=self.run_id)
        # Resolve markouts whose horizons already elapsed before stopping the poller.
        try:
            self.markout.poll(utc_ms(), self.mid_histories)
        except Exception as exc:  # noqa: BLE001
            log.warning("final markout drain failed: %s", exc)
        try:
            # Final analysis + upload (rotates/closes parts internally)
            await asyncio.to_thread(self._sync_and_analyze, final=True)
            self.lock.renew(self.run_id, git_sha=self.settings.git_sha)
            self.store.close()
            # Keep status=running on SIGTERM/deploy so the next revision can resume.
            # Only mark completed when the observation target is reached.
            status = "completed" if self._completed else "running"
            self.state.status = status
            self._write_state()
            self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
            self.backend.upload_json(
                self.sync.active_pointer_key(),
                {
                    "run_id": self.run_id,
                    "status": status,
                    "updated_at": now_iso(),
                    "git_sha": self.settings.git_sha,
                    "shutdown_reason": "completed" if self._completed else "preempted_or_signal",
                },
            )
            self.meta.end_run(self.run_id, status="completed" if self._completed else "stopped")
        except Exception as exc:  # noqa: BLE001
            log.exception("shutdown flush failed: %s", exc)
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
        self._write_state()
        if flush_upload:
            await asyncio.to_thread(self._sync_and_analyze, final=False)
        else:
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

    def _sync_and_analyze(self, *, final: bool) -> None:
        self.store.maybe_flush()
        # Close current parts so durable storage gets complete files only.
        self.store.rotate_all()
        closed = self.store.take_closed_paths()
        try:
            uploaded = self.sync.upload_new_parquets(self.settings.data_dir, paths=closed)
        except Exception:
            self.store.requeue_closed_paths(closed)
            raise
        self.lock.renew(self.run_id, git_sha=self.settings.git_sha)
        log_event(
            log,
            "gcs_uploaded",
            f"uploaded {len(uploaded)} parquet objects",
            run_id=self.run_id,
            detail=str(len(uploaded)),
        )
        # Lightweight dashboard JSON
        hours = self.hours or 72.0
        # For short runs use elapsed observation window
        try:
            started = datetime.fromisoformat(self.state.started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed = max((datetime.now(UTC) - started).total_seconds() / 3600.0, 0.05)
        except ValueError:
            elapsed = 1.0
        # Use the full target window only when the observation truly completed.
        # SIGTERM/deploy also calls final=True; analyzing a 72h window at hour 5
        # collapses coverage and wipes candidates until the next mid-run sync.
        window = (
            hours
            if final and self._completed and self.hours
            else min(hours, max(elapsed, 0.1))
        )
        public_ok = False
        prev_flush = self.state.last_successful_flush
        try:
            from lighter_mm.cloud.estimate import estimate_storage

            est = estimate_storage(
                bytes_so_far=self.sync.bytes_uploaded
                or _dir_size(self.settings.data_dir),
                elapsed_hours=max(elapsed, 1 / 60),
            )
            # Advance flush before building the overview so baked status/last_update
            # reflect this publish (otherwise latest.json stays OFFLINE forever while
            # generated_at moves — detail-upload failures used to block the flush).
            flush_at = now_iso()
            self.state.last_successful_flush = flush_at
            ws_runtime = None
            if self._ws is not None:
                ws_runtime = self._ws.runtime.public_dict()
                log_event(
                    log,
                    "ws_runtime",
                    (
                        f"shards={ws_runtime.get('connected_shards')}/"
                        f"{ws_runtime.get('total_shards')} "
                        f"channels={ws_runtime.get('subscribed_channels')} "
                        f"sub_err={ws_runtime.get('subscription_errors')} "
                        f"trade_parse_err={ws_runtime.get('trade_parse_errors')}"
                    ),
                    run_id=self.run_id,
                )
            payload = build_dashboard_payload(
                self.settings,
                hours=window,
                state=self.state,
                storage_estimate=est,
                ws_runtime=ws_runtime,
                last_book_sample_at_ms=self._last_usable_book_sample_ts,
                last_book_row_at_ms=self._last_book_row_written_ts,
                trades_without_reference_mid=self._trades_without_reference_mid,
            )
            # Core dashboard JSON — this alone must count as a successful flush.
            self.backend.upload_json(
                self.sync.public_key("latest.json"), payload["latest"], public=True
            )
            self.backend.upload_json(
                self.sync.public_key("markets.json"),
                {"markets": payload["markets"], "generated_at": payload["latest"]["generated_at"]},
                public=True,
            )
            self.backend.upload_json(
                self.sync.public_key("candidates.json"),
                {"candidates": payload["candidates"]},
                public=True,
            )
            self.backend.upload_json(
                f"{self.sync.run_prefix()}/reports/latest.json", payload["latest"]
            )
            self.state.last_analysis_at = flush_at
            public_ok = True
            log_event(log, "analysis_completed", "dashboard JSON refreshed", run_id=self.run_id)

            # Per-market details are best-effort. A single market/{sym}.json failure
            # must not roll back last_successful_flush / mark the collector OFFLINE.
            detail_failures = 0
            for sym, detail in payload["market_details"].items():
                try:
                    self.backend.upload_json(
                        self.sync.public_key(f"market/{sym}.json"), detail, public=True
                    )
                except Exception as detail_exc:  # noqa: BLE001
                    detail_failures += 1
                    log.warning(
                        "market detail upload failed for %s: %s", sym, detail_exc
                    )
            if detail_failures:
                log.warning(
                    "market detail uploads: %s failed of %s",
                    detail_failures,
                    len(payload["market_details"]),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("analysis/dashboard generation failed: %s", exc)
            if not public_ok:
                self.state.last_successful_flush = prev_flush
                self.state.last_analysis_at = None
            # Best-effort: still move the public latest.json clock so the UI does
            # not stay forever COLLECTING on a frozen object from a prior revision.
            try:
                self._publish_public_failure_status(str(exc))
            except Exception as pub_exc:  # noqa: BLE001
                log.warning("public failure-status publish failed: %s", pub_exc)

        self._write_state()
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

    def _heartbeat_running(self, note: str) -> None:
        """Advance active_run/state (+ lightweight public latest) without DuckDB.

        Used during resume so the dashboard git_sha / generated_at move even when
        full analysis is still pending or OOMs on a small Worker Pool.
        """
        self._write_state()
        now = now_iso()
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
        # Do not claim a successful flush — keep last_successful_flush truthful.
        # Still move generated_at/git_sha so ops can see the new revision is alive.
        try:
            obs_hours = None
            try:
                started = datetime.fromisoformat(self.state.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                obs_hours = (datetime.now(UTC) - started).total_seconds() / 3600.0
            except ValueError:
                obs_hours = None
            payload = {
                "title": "Lighter MM Scanner",
                "status": "COLLECTING",
                "run_id": self.run_id,
                "started_at": self.state.started_at,
                "observation_hours": obs_hours,
                "observation_target_hours": self.state.observation_target_hours,
                "markets": 0,
                "markets_analyzed": 0,
                "markets_discovered": len(self.state.markets or []),
                "candidates": 0,
                "coverage_pct": None,
                "last_update": self.state.last_successful_flush,
                "last_successful_flush": self.state.last_successful_flush,
                "last_trade_at": None,
                "last_book_sample_at": None,
                "git_sha": self.settings.git_sha,
                "collector_version": self.settings.collector_version,
                "top_candidate": None,
                "analysis_error": None,
                "health_warnings": [note],
                "samples_written": self.state.samples_written,
                "ws": (
                    self._ws.runtime.public_dict()
                    if getattr(self, "_ws", None) is not None
                    else None
                ),
                "generated_at": now,
                "read_only": True,
                "disclaimer": (
                    "READ-ONLY research. Displayed spread × trade count ≠ profit. "
                    "No trading / no wallet / no API keys."
                ),
            }
            self.backend.upload_json(
                self.sync.public_key("latest.json"), payload, public=True
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat public latest.json failed: %s", exc)
        log_event(
            log,
            "collector_heartbeat",
            note,
            run_id=self.run_id,
            git_sha=self.settings.git_sha,
        )

    def _publish_public_failure_status(self, error: str) -> None:
        """Overwrite public latest.json with an explicit ERROR/DEGRADED payload."""
        self._write_state()
        now = now_iso()
        payload = {
            "title": "Lighter MM Scanner",
            "status": "ERROR",
            "run_id": self.run_id,
            "started_at": self.state.started_at,
            "observation_hours": None,
            "observation_target_hours": self.state.observation_target_hours,
            "markets": 0,
            "markets_analyzed": 0,
            "markets_discovered": len(self.state.markets or []),
            "candidates": 0,
            "coverage_pct": None,
            "last_update": now,
            "last_successful_flush": self.state.last_successful_flush,
            "last_trade_at": None,
            "last_book_sample_at": None,
            "git_sha": self.settings.git_sha,
            "collector_version": self.settings.collector_version,
            "top_candidate": None,
            "analysis_error": error,
            "health_warnings": [error],
            "samples_written": self.state.samples_written,
            "ws": (
                self._ws.runtime.public_dict()
                if getattr(self, "_ws", None) is not None
                else None
            ),
            "generated_at": now,
            "read_only": True,
            "disclaimer": (
                "READ-ONLY research. Displayed spread × trade count ≠ profit. "
                "No trading / no wallet / no API keys."
            ),
        }
        self.backend.upload_json(self.sync.public_key("latest.json"), payload, public=True)

    async def _durable_sync_loop(self) -> None:
        # First full analyze shortly after WS is up. Startup only heartbeats —
        # blocking DuckDB before the main loops caused resume crash-loops.
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
                # Renew immediately before heavy analyze; lease is also kept by
                # _lock_renew_loop, but analyze can exceed one lease interval.
                self.lock.renew(self.run_id, git_sha=self.settings.git_sha)
                await asyncio.to_thread(self._sync_and_analyze, final=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("durable sync failed: %s", exc)
                try:
                    await asyncio.to_thread(
                        self._heartbeat_running, f"durable sync failed: {exc}"
                    )
                except Exception as hb_exc:  # noqa: BLE001
                    log.warning("sync-failure heartbeat failed: %s", hb_exc)

    async def _analysis_loop(self) -> None:
        # Analysis is bundled into durable sync; keep a lighter heartbeat state write
        interval = min(60.0, self.settings.analysis_interval_minutes * 60)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                self._write_state()
                self.backend.upload_json(self.sync.state_key(), self.state.to_public_dict())
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
        ref = None
        if hist is not None:
            pt = hist.nearest_at_or_before(trade.timestamp_ms)
            if (
                pt is not None
                and 0 <= trade.timestamp_ms - pt.ts_ms <= 3000
            ):
                ref = pt.mid
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
                self._last_book_row_written_ts = now
                if book.synced and not metrics.is_stale and metrics.mid is not None:
                    self._last_usable_book_sample_ts = now
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
            if m.get("is_stale"):
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


def _dir_size(path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


async def run_collector(settings: Settings, hours: float | None = None) -> None:
    app = CollectorApp(settings, hours=hours)
    await app.run()
