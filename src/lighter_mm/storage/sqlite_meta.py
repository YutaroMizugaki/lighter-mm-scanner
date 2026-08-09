"""SQLite for metadata / run state / data-quality counters."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lighter_mm.models import MarketMeta


class SqliteMeta:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                hours REAL,
                status TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS markets (
                market_id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                maker_fee TEXT,
                taker_fee TEXT,
                min_base_amount TEXT,
                min_quote_amount TEXT,
                supported_price_decimals INTEGER,
                supported_size_decimals INTEGER,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_dq (
                market_id INTEGER PRIMARY KEY,
                expected_samples INTEGER NOT NULL DEFAULT 0,
                actual_samples INTEGER NOT NULL DEFAULT 0,
                coverage_pct REAL NOT NULL DEFAULT 0,
                ws_disconnect_count INTEGER NOT NULL DEFAULT 0,
                book_resync_count INTEGER NOT NULL DEFAULT 0,
                nonce_gap_count INTEGER NOT NULL DEFAULT 0,
                stale_book_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def upsert_markets(self, markets: list[MarketMeta]) -> None:
        now = datetime.now(UTC).isoformat()
        cur = self._conn.cursor()
        for m in markets:
            cur.execute(
                """
                INSERT INTO markets (
                  market_id, symbol, status, maker_fee, taker_fee,
                  min_base_amount, min_quote_amount,
                  supported_price_decimals, supported_size_decimals,
                  raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                  symbol=excluded.symbol,
                  status=excluded.status,
                  maker_fee=excluded.maker_fee,
                  taker_fee=excluded.taker_fee,
                  min_base_amount=excluded.min_base_amount,
                  min_quote_amount=excluded.min_quote_amount,
                  supported_price_decimals=excluded.supported_price_decimals,
                  supported_size_decimals=excluded.supported_size_decimals,
                  raw_json=excluded.raw_json,
                  updated_at=excluded.updated_at
                """,
                (
                    m.market_id,
                    m.symbol,
                    m.status.value,
                    str(m.maker_fee),
                    str(m.taker_fee),
                    str(m.min_base_amount),
                    str(m.min_quote_amount),
                    m.supported_price_decimals,
                    m.supported_size_decimals,
                    m.model_dump_json(),
                    now,
                ),
            )
        self._conn.commit()

    def start_run(self, run_id: str, hours: float | None) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO runs (run_id, started_at, hours, status)
            VALUES (?, ?, ?, 'running')
            ON CONFLICT(run_id) DO UPDATE SET status='running', ended_at=NULL
            """,
            (run_id, now, hours),
        )
        self.set_kv("active_run_id", run_id)
        self._conn.commit()

    def end_run(self, run_id: str, status: str = "stopped") -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE runs SET ended_at=?, status=? WHERE run_id=?",
            (now, status, run_id),
        )
        self._conn.commit()

    def get_active_run(self) -> dict[str, Any] | None:
        run_id = self.get_kv("active_run_id")
        if not run_id:
            return None
        row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO kv(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def update_dq(self, market_id: int, **fields: Any) -> None:
        self.update_dq_batch([(market_id, fields)])

    def update_dq_batch(self, rows: list[tuple[int, dict[str, Any]]]) -> None:
        """Update DQ counters for many markets in a single transaction."""
        if not rows:
            return
        now = datetime.now(UTC).isoformat()
        cur = self._conn.cursor()
        cur.execute("BEGIN")
        try:
            for market_id, fields in rows:
                cur.execute(
                    "INSERT OR IGNORE INTO market_dq(market_id, updated_at) VALUES (?, ?)",
                    (market_id, now),
                )
                if fields:
                    cols = ", ".join(f"{k}=?" for k in fields)
                    cur.execute(
                        f"UPDATE market_dq SET {cols}, updated_at=? WHERE market_id=?",
                        (*fields.values(), now, market_id),
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def bump_dq(self, market_id: int, field: str, n: int = 1) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO market_dq(market_id, updated_at) VALUES (?, ?)",
            (market_id, now),
        )
        self._conn.execute(
            f"UPDATE market_dq SET {field} = {field} + ?, updated_at=? WHERE market_id=?",
            (n, now, market_id),
        )
        self._conn.commit()

    def all_dq(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM market_dq").fetchall()
        return [dict(r) for r in rows]

    def status_summary(self) -> dict[str, Any]:
        run = self.get_active_run()
        n_markets = self._conn.execute("SELECT COUNT(*) AS c FROM markets").fetchone()["c"]
        dq = self.all_dq()
        return {
            "active_run": run,
            "markets": n_markets,
            "dq": dq,
            "counters": json.loads(self.get_kv("counters") or "{}"),
        }
