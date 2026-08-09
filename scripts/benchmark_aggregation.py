#!/usr/bin/env python3
"""Lightweight aggregation benchmark — verifies volatility pairing does not explode."""

from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_window
from lighter_mm.config import Settings


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KB; macOS reports bytes
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def _write_synthetic(data_dir: Path, *, markets: int, rows_per_market: int) -> int:
    books = data_dir / "book_samples" / "date=2026-08-09" / "hour=12"
    books.mkdir(parents=True, exist_ok=True)
    interval_ms = 5000
    base = int(time.time() * 1000) - rows_per_market * interval_ms
    rows = []
    for mid in range(markets):
        for i in range(rows_per_market):
            ts = base + i * interval_ms
            mid_px = 100.0 + (i % 7) * 0.1
            rows.append(
                {
                    "timestamp_ms": ts,
                    "market_id": mid,
                    "symbol": f"M{mid}",
                    "is_stale": False,
                    "spread_bps": 5.0,
                    "mid": mid_px,
                    "best_bid_size_usd": 100.0,
                    "best_ask_size_usd": 100.0,
                    "two_sided_depth_5bps_usd": 50.0,
                    "two_sided_depth_10bps_usd": 200.0,
                    "two_sided_depth_25bps_usd": 400.0,
                    "current_funding_rate": None,
                    "funding_rate": None,
                    "open_interest": None,
                    "daily_quote_token_volume": None,
                }
            )
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, books / "part-0.parquet")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark DuckDB aggregation pipeline")
    parser.add_argument("--markets", type=int, default=50, help="Number of synthetic markets")
    parser.add_argument(
        "--rows-per-market",
        type=int,
        default=400,
        help="Book sample rows per market (default ~20k total at 50x400)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("/tmp/lighter-mm-bench"),
        help="Working directory for synthetic parquet + reports",
    )
    parser.add_argument(
        "--rss-limit-mb",
        type=float,
        default=800.0,
        help="Exit with error if peak RSS exceeds this (0 disables)",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "book_samples").mkdir(exist_ok=True)

    markets = args.markets
    rows_per_market = args.rows_per_market
    total = _write_synthetic(data_dir, markets=markets, rows_per_market=rows_per_market)

    settings = Settings(data_dir=data_dir, reports_dir=data_dir / "reports")
    t0 = time.monotonic()
    result = analyze_window(settings, hours=1.0)
    elapsed = time.monotonic() - t0
    rss = _peak_rss_mb()
    rows_per_second = total / elapsed if elapsed > 0 else 0.0

    print(f"rows={total}")
    print(f"markets={markets}")
    print(f"rows_per_market={rows_per_market}")
    print(f"elapsed_s={elapsed:.3f}")
    print(f"peak_rss_mb={rss:.1f}")
    print(f"book_row_count={result.get('book_row_count')}")
    print(f"rows_per_second={rows_per_second:.0f}")
    if result.get("error"):
        raise SystemExit(f"analysis error: {result['error']}")
    if args.rss_limit_mb > 0 and rss > args.rss_limit_mb:
        raise SystemExit(f"peak RSS too high: {rss:.1f} MB (limit {args.rss_limit_mb:.0f})")


if __name__ == "__main__":
    main()
