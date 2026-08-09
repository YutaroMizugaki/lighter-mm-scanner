"""Buffered Parquet writers with configurable rotation and flush-on-stop."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.storage.parquet_validation import validate_parquet_file

log = logging.getLogger(__name__)


class _RotatingWriter:
    def __init__(
        self,
        root: Path,
        dataset: str,
        schema: pa.Schema,
        flush_rows: int,
        flush_seconds: float,
        rotation_minutes: int = 60,
    ) -> None:
        self.root = root
        self.dataset = dataset
        self.schema = schema
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self.rotation_minutes = max(1, rotation_minutes)
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._writer: pq.ParquetWriter | None = None
        self._current_path: Path | None = None
        self._tmp_path: Path | None = None
        self._current_part: str | None = None
        self._current_date: str | None = None
        self._current_hour: str | None = None
        self.rows_written = 0
        self._closed_paths: list[Path] = []

    def append(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._buffer.append(row)
            if (
                len(self._buffer) >= self.flush_rows
                or (time.monotonic() - self._last_flush) >= self.flush_seconds
            ):
                self._flush_unlocked()

    def maybe_flush(self) -> None:
        with self._lock:
            if self._buffer and (time.monotonic() - self._last_flush) >= self.flush_seconds:
                self._flush_unlocked()

    def rotate_now(self) -> None:
        """Force close current part file so durable sync can upload it."""
        with self._lock:
            self._flush_unlocked()
            self._close_writer_unlocked()

    def close(self) -> None:
        with self._lock:
            self._flush_unlocked()
            self._close_writer_unlocked()

    def take_closed_paths(self) -> list[Path]:
        """Return and clear completed part files safe to upload."""
        with self._lock:
            paths = list(self._closed_paths)
            self._closed_paths.clear()
            return paths

    def requeue_closed_paths(self, paths: list[Path]) -> None:
        """Return paths to the closed set after a failed upload attempt."""
        root = self.root / self.dataset
        with self._lock:
            for path in paths:
                try:
                    path.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                if path not in self._closed_paths and path != self._current_path:
                    self._closed_paths.append(path)

    def open_path(self) -> Path | None:
        with self._lock:
            return self._current_path

    def _close_writer_unlocked(self) -> None:
        if self._writer is None:
            return
        self._writer.close()
        self._writer = None
        if self._tmp_path is not None and self._current_path is not None:
            self._finalize_parquet_unlocked(self._tmp_path, self._current_path)
        self._current_path = None
        self._tmp_path = None
        self._current_part = None
        self._current_date = None
        self._current_hour = None

    def _finalize_parquet_unlocked(self, tmp_path: Path, final_path: Path) -> None:
        ok, err = validate_parquet_file(tmp_path)
        if not ok:
            log.error(
                "parquet write validation failed path=%s error=%s",
                tmp_path,
                err,
            )
            try:
                tmp_path.unlink()
            except OSError as unlink_exc:
                log.warning("failed to remove invalid temp parquet %s: %s", tmp_path, unlink_exc)
            return
        try:
            os.replace(tmp_path, final_path)
        except OSError as exc:
            log.error(
                "parquet atomic rename failed tmp=%s final=%s error=%s",
                tmp_path,
                final_path,
                exc,
            )
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return
        self._closed_paths.append(final_path)
        log.debug("finalized parquet %s", final_path)

    def _part_key(self, ts_ms: int | None) -> tuple[str, str, str]:
        if ts_ms is None:
            dt = datetime.now(UTC)
        else:
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
        bucket = (dt.minute // self.rotation_minutes) * self.rotation_minutes
        date = dt.strftime("%Y-%m-%d")
        hour = dt.strftime("%H")
        part = f"{dt.strftime('%Y%m%d_%H')}{bucket:02d}"
        return date, hour, part

    def _ensure_writer(self, date: str, hour: str, part: str) -> None:
        if (
            self._writer is not None
            and self._current_part == part
            and self._current_date == date
            and self._current_hour == hour
        ):
            return
        if self._writer is not None:
            self._close_writer_unlocked()
        out_dir = self.root / self.dataset / f"date={date}" / f"hour={hour}"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = uuid.uuid4().hex[:12]
        final_path = out_dir / f"part-{part}-{suffix}.parquet"
        tmp_path = final_path.with_suffix(".parquet.tmp")
        self._writer = pq.ParquetWriter(tmp_path, self.schema, compression="zstd")
        self._current_path = final_path
        self._tmp_path = tmp_path
        self._current_part = part
        self._current_date = date
        self._current_hour = hour
        log.debug("opened parquet tmp=%s final=%s", tmp_path, final_path)

    def _flush_unlocked(self) -> None:
        if not self._buffer:
            return
        by_part: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in self._buffer:
            key = self._part_key(row.get("timestamp_ms"))
            by_part.setdefault(key, []).append(row)
        self._buffer.clear()
        for (date, hour, part), rows in by_part.items():
            self._ensure_writer(date, hour, part)
            assert self._writer is not None
            table = pa.Table.from_pylist(rows, schema=self.schema)
            self._writer.write_table(table)
            self.rows_written += len(rows)
        self._last_flush = time.monotonic()


def _book_schema(depth_levels: list[int]) -> pa.Schema:
    fields = [
        ("timestamp_ms", pa.int64()),
        ("market_id", pa.int32()),
        ("symbol", pa.string()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("mid", pa.float64()),
        ("spread_absolute", pa.float64()),
        ("spread_bps", pa.float64()),
        ("best_bid_size_base", pa.float64()),
        ("best_ask_size_base", pa.float64()),
        ("best_bid_size_usd", pa.float64()),
        ("best_ask_size_usd", pa.float64()),
        ("is_stale", pa.bool_()),
        ("is_usable", pa.bool_()),
        ("is_inactive", pa.bool_()),
        ("book_update_age_ms", pa.int64()),
        ("nonce", pa.int64()),
        ("index_price", pa.float64()),
        ("mark_price", pa.float64()),
        ("stats_mid_price", pa.float64()),
        ("open_interest", pa.float64()),
        ("last_trade_price", pa.float64()),
        ("current_funding_rate", pa.float64()),
        ("funding_rate", pa.float64()),
        ("daily_base_token_volume", pa.float64()),
        ("daily_quote_token_volume", pa.float64()),
        ("daily_price_low", pa.float64()),
        ("daily_price_high", pa.float64()),
        ("daily_price_change", pa.float64()),
    ]
    for bps in depth_levels:
        fields.append((f"bid_depth_{bps}bps_usd", pa.float64()))
        fields.append((f"ask_depth_{bps}bps_usd", pa.float64()))
        fields.append((f"two_sided_depth_{bps}bps_usd", pa.float64()))
    return pa.schema(fields)


TRADE_SCHEMA = pa.schema(
    [
        ("timestamp_ms", pa.int64()),
        ("market_id", pa.int32()),
        ("symbol", pa.string()),
        ("trade_id", pa.int64()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("usd_amount", pa.float64()),
        ("is_maker_ask", pa.bool_()),
        ("taker_is_buy", pa.bool_()),
        ("type", pa.string()),
        ("reference_mid", pa.float64()),
    ]
)

MARKOUT_SCHEMA = pa.schema(
    [
        ("timestamp_ms", pa.int64()),
        ("market_id", pa.int32()),
        ("symbol", pa.string()),
        ("trade_id", pa.int64()),
        ("horizon_s", pa.int32()),
        ("trade_price", pa.float64()),
        ("reference_mid", pa.float64()),
        ("future_mid", pa.float64()),
        ("maker_markout_bps", pa.float64()),
        ("is_maker_ask", pa.bool_()),
    ]
)


class ParquetStore:
    def __init__(
        self,
        data_dir: Path,
        depth_levels: list[int],
        flush_rows: int = 500,
        flush_seconds: float = 5.0,
        rotation_minutes: int = 15,
    ) -> None:
        self.data_dir = data_dir
        self.book = _RotatingWriter(
            data_dir,
            "book_samples",
            _book_schema(depth_levels),
            flush_rows,
            flush_seconds,
            rotation_minutes,
        )
        self.trades = _RotatingWriter(
            data_dir, "trades", TRADE_SCHEMA, flush_rows, flush_seconds, rotation_minutes
        )
        self.markouts = _RotatingWriter(
            data_dir, "markouts", MARKOUT_SCHEMA, flush_rows, flush_seconds, rotation_minutes
        )

    def write_book(self, row: dict[str, Any]) -> None:
        self.book.append(row)

    def write_trade(self, row: dict[str, Any]) -> None:
        self.trades.append(row)

    def write_markout(self, row: dict[str, Any]) -> None:
        self.markouts.append(row)

    def maybe_flush(self) -> None:
        self.book.maybe_flush()
        self.trades.maybe_flush()
        self.markouts.maybe_flush()

    def rotate_all(self) -> None:
        self.book.rotate_now()
        self.trades.rotate_now()
        self.markouts.rotate_now()

    def take_closed_paths(self) -> list[Path]:
        """Closed Parquet parts across datasets (safe for durable upload)."""
        return (
            self.book.take_closed_paths()
            + self.trades.take_closed_paths()
            + self.markouts.take_closed_paths()
        )

    def requeue_closed_paths(self, paths: list[Path]) -> None:
        self.book.requeue_closed_paths(paths)
        self.trades.requeue_closed_paths(paths)
        self.markouts.requeue_closed_paths(paths)

    def close(self) -> None:
        self.book.close()
        self.trades.close()
        self.markouts.close()

    @property
    def samples_written(self) -> int:
        return self.book.rows_written

    @property
    def trades_written(self) -> int:
        return self.trades.rows_written

    @property
    def markouts_written(self) -> int:
        return self.markouts.rows_written
