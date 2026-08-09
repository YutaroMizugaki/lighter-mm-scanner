"""Buffered Parquet writers with hourly rotation and flush-on-stop."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)


class _RotatingWriter:
    def __init__(
        self,
        root: Path,
        dataset: str,
        schema: pa.Schema,
        flush_rows: int,
        flush_seconds: float,
    ) -> None:
        self.root = root
        self.dataset = dataset
        self.schema = schema
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._writer: pq.ParquetWriter | None = None
        self._current_hour: str | None = None
        self._current_date: str | None = None
        self.rows_written = 0

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

    def close(self) -> None:
        with self._lock:
            self._flush_unlocked()
            if self._writer is not None:
                self._writer.close()
                self._writer = None

    def _hour_key(self, ts_ms: int | None) -> tuple[str, str]:
        if ts_ms is None:
            dt = datetime.now(UTC)
        else:
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y%m%d_%H")

    def _ensure_writer(self, date: str, hour: str) -> None:
        if self._writer is not None and self._current_hour == hour and self._current_date == date:
            return
        if self._writer is not None:
            self._writer.close()
        out_dir = self.root / self.dataset / f"date={date}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"part-{hour}.parquet"
        # Append-compatible: open new file per hour with unique suffix if exists and open
        if path.exists() and self._current_hour != hour:
            # Continuing same hour after restart — use new part file
            path = out_dir / f"part-{hour}-{int(time.time())}.parquet"
        elif path.exists() and self._writer is None:
            path = out_dir / f"part-{hour}-{int(time.time())}.parquet"
        self._writer = pq.ParquetWriter(path, self.schema, compression="zstd")
        self._current_hour = hour
        self._current_date = date
        log.debug("opened parquet %s", path)

    def _flush_unlocked(self) -> None:
        if not self._buffer:
            return
        # Group by hour for correct partition
        by_hour: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in self._buffer:
            key = self._hour_key(row.get("timestamp_ms"))
            by_hour.setdefault(key, []).append(row)
        self._buffer.clear()
        for (date, hour), rows in by_hour.items():
            self._ensure_writer(date, hour)
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
    ) -> None:
        self.data_dir = data_dir
        self.book = _RotatingWriter(
            data_dir, "book_samples", _book_schema(depth_levels), flush_rows, flush_seconds
        )
        self.trades = _RotatingWriter(
            data_dir, "trades", TRADE_SCHEMA, flush_rows, flush_seconds
        )
        self.markouts = _RotatingWriter(
            data_dir, "markouts", MARKOUT_SCHEMA, flush_rows, flush_seconds
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
