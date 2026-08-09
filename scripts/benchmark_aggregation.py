#!/usr/bin/env python3
"""Aggregation benchmark — analysis-only RSS with full-window coverage."""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import analyze_window
from lighter_mm.config import Settings

DEFAULT_DATA_DIR = Path("/tmp/lighter-mm-bench")
META_FILENAME = "benchmark_meta.json"
SAMPLE_INTERVAL_SECONDS = 5.0
CHUNK_ROWS = 50_000
MIN_ANALYZED_RATIO = 0.95


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def _is_safe_benchmark_dir(data_dir: Path) -> bool:
    resolved = data_dir.resolve()
    tmp = Path("/tmp").resolve()
    try:
        resolved.relative_to(tmp)
    except ValueError:
        return False
    name = resolved.name
    return name == "lighter-mm-bench" or name.startswith("lighter-mm-bench-")


def _prepare_data_dir(data_dir: Path, *, clean: bool) -> None:
    if clean:
        if not _is_safe_benchmark_dir(data_dir):
            raise SystemExit(
                f"refusing to clean unsafe data-dir {data_dir}; "
                "use /tmp/lighter-mm-bench or /tmp/lighter-mm-bench-*"
            )
        if data_dir.exists():
            shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "book_samples").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)


def _partition_path(data_dir: Path, timestamp_ms: int) -> Path:
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
    return (
        data_dir
        / "book_samples"
        / f"date={dt.strftime('%Y-%m-%d')}"
        / f"hour={dt.hour:02d}"
    )


def _write_chunk(data_dir: Path, rows: list[dict], writers: dict[Path, pq.ParquetWriter]) -> None:
    if not rows:
        return
    by_partition: dict[Path, list[dict]] = {}
    for row in rows:
        part = _partition_path(data_dir, int(row["timestamp_ms"]))
        by_partition.setdefault(part, []).append(row)
    for part, part_rows in by_partition.items():
        part.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(part_rows)
        writer = writers.get(part)
        if writer is None:
            part_file = part / f"part-{len(list(part.glob('part-*.parquet'))):05d}.parquet"
            writer = pq.ParquetWriter(part_file, table.schema)
            writers[part] = writer
        writer.write_table(table)


def generate_synthetic(
    data_dir: Path,
    *,
    markets: int,
    rows_per_market: int,
    sample_interval_seconds: float = SAMPLE_INTERVAL_SECONDS,
    clean: bool = True,
) -> dict:
    _prepare_data_dir(data_dir, clean=clean)
    interval_ms = int(sample_interval_seconds * 1000)
    generated_duration_hours = rows_per_market * sample_interval_seconds / 3600.0
    base_ts = int(time.time() * 1000) - rows_per_market * interval_ms

    writers: dict[Path, pq.ParquetWriter] = {}
    chunk: list[dict] = []
    generated_rows = 0
    try:
        for mid in range(markets):
            for i in range(rows_per_market):
                ts = base_ts + i * interval_ms
                mid_px = 100.0 + (i % 7) * 0.1
                chunk.append(
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
                generated_rows += 1
                if len(chunk) >= CHUNK_ROWS:
                    _write_chunk(data_dir, chunk, writers)
                    chunk = []
        _write_chunk(data_dir, chunk, writers)
    finally:
        for writer in writers.values():
            writer.close()

    meta = {
        "generated_rows": generated_rows,
        "markets": markets,
        "rows_per_market": rows_per_market,
        "sample_interval_seconds": sample_interval_seconds,
        "generated_duration_hours": generated_duration_hours,
        "base_timestamp_ms": base_ts,
        "end_timestamp_ms": base_ts + (rows_per_market - 1) * interval_ms,
    }
    (data_dir / META_FILENAME).write_text(json.dumps(meta, indent=2))
    return meta


def _load_meta(data_dir: Path) -> dict:
    meta_path = data_dir / META_FILENAME
    if not meta_path.exists():
        raise SystemExit(f"missing {meta_path}; run generate first")
    return json.loads(meta_path.read_text())


def _resolve_analysis_hours(analysis_hours: str, meta: dict) -> float:
    if analysis_hours == "auto":
        return float(meta["generated_duration_hours"]) + 0.1
    return float(analysis_hours)


def analyze_dataset(
    data_dir: Path,
    *,
    analysis_hours: str = "auto",
    rss_limit_mb: float = 0.0,
    min_analyzed_ratio: float = MIN_ANALYZED_RATIO,
    explain_volatility: bool = False,
    benchmark_profile: bool = True,
) -> dict:
    meta = _load_meta(data_dir)
    hours = _resolve_analysis_hours(analysis_hours, meta)
    settings = Settings(data_dir=data_dir, reports_dir=data_dir / "reports")

    t0 = time.monotonic()
    result = analyze_window(
        settings,
        hours,
        benchmark_profile=benchmark_profile,
        explain_volatility=explain_volatility,
    )
    elapsed = time.monotonic() - t0
    rss = _peak_rss_mb()

    generated_rows = int(meta["generated_rows"])
    analyzed_rows = int(result.get("book_row_count") or 0)
    ratio = analyzed_rows / generated_rows if generated_rows > 0 else 0.0
    rows_per_second = analyzed_rows / elapsed if elapsed > 0 else 0.0

    output = {
        "generated_rows": generated_rows,
        "analyzed_book_rows": analyzed_rows,
        "analyzed_ratio": ratio,
        "markets": meta["markets"],
        "rows_per_market": meta["rows_per_market"],
        "sample_interval_seconds": meta["sample_interval_seconds"],
        "generated_duration_hours": meta["generated_duration_hours"],
        "analysis_window_hours": hours,
        "analysis_elapsed_s": elapsed,
        "analysis_peak_rss_mb": rss,
        "analysis_rows_per_second": rows_per_second,
    }
    if benchmark_profile and "benchmark_profile" in result:
        output["phase_rss_mb"] = result["benchmark_profile"]
    if explain_volatility and "volatility_explain" in result:
        output["volatility_explain"] = result["volatility_explain"]

    _print_output(output)
    if result.get("error"):
        raise SystemExit(f"analysis error: {result['error']}")
    if ratio < min_analyzed_ratio:
        raise SystemExit(
            f"analyzed_ratio {ratio:.4f} < {min_analyzed_ratio:.2f} "
            f"(generated={generated_rows}, analyzed={analyzed_rows})"
        )
    if rss_limit_mb > 0 and rss > rss_limit_mb:
        raise SystemExit(f"analysis_peak_rss_mb {rss:.1f} exceeds limit {rss_limit_mb:.0f}")
    return output


def _print_output(output: dict) -> None:
    print(f"generated_rows={output['generated_rows']}")
    print(f"analyzed_book_rows={output['analyzed_book_rows']}")
    print(f"analyzed_ratio={output['analyzed_ratio']:.4f}")
    print(f"markets={output['markets']}")
    print(f"rows_per_market={output['rows_per_market']}")
    print(f"sample_interval_seconds={output['sample_interval_seconds']}")
    print(f"generated_duration_hours={output['generated_duration_hours']:.3f}")
    print(f"analysis_window_hours={output['analysis_window_hours']:.3f}")
    print(f"analysis_elapsed_s={output['analysis_elapsed_s']:.3f}")
    print(f"analysis_peak_rss_mb={output['analysis_peak_rss_mb']:.1f}")
    print(f"analysis_rows_per_second={output['analysis_rows_per_second']:.0f}")
    phase = output.get("phase_rss_mb")
    if phase:
        for key, val in phase.items():
            print(f"{key}={val:.1f}")


def cmd_generate(args: argparse.Namespace) -> None:
    meta = generate_synthetic(
        args.data_dir,
        markets=args.markets,
        rows_per_market=args.rows_per_market,
        sample_interval_seconds=args.sample_interval_seconds,
        clean=not args.no_clean,
    )
    print(f"generated_rows={meta['generated_rows']}")
    print(f"generated_duration_hours={meta['generated_duration_hours']:.3f}")


def cmd_analyze(args: argparse.Namespace) -> None:
    analyze_dataset(
        args.data_dir,
        analysis_hours=args.analysis_hours,
        rss_limit_mb=args.rss_limit_mb,
        min_analyzed_ratio=args.min_analyzed_ratio,
        explain_volatility=args.explain_volatility,
        benchmark_profile=not args.no_profile,
    )


def cmd_run(args: argparse.Namespace) -> None:
    script = str(Path(__file__).resolve())
    gen_cmd = [
        sys.executable,
        script,
        "generate",
        "--data-dir",
        str(args.data_dir),
        "--markets",
        str(args.markets),
        "--rows-per-market",
        str(args.rows_per_market),
        "--sample-interval-seconds",
        str(args.sample_interval_seconds),
    ]
    if args.no_clean:
        gen_cmd.append("--no-clean")
    proc = subprocess.run(gen_cmd, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)

    analyze_cmd = [
        sys.executable,
        script,
        "analyze",
        "--data-dir",
        str(args.data_dir),
        "--analysis-hours",
        args.analysis_hours,
        "--min-analyzed-ratio",
        str(args.min_analyzed_ratio),
        "--rss-limit-mb",
        str(args.rss_limit_mb),
    ]
    if args.explain_volatility:
        analyze_cmd.append("--explain-volatility")
    if args.no_profile:
        analyze_cmd.append("--no-profile")
    proc = subprocess.run(analyze_cmd, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Benchmark working directory (default: /tmp/lighter-mm-bench)",
    )
    parser.add_argument("--markets", type=int, default=50)
    parser.add_argument("--rows-per-market", type=int, default=400)
    parser.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=SAMPLE_INTERVAL_SECONDS,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark DuckDB aggregation pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Write synthetic parquet (chunked, memory-safe)")
    _add_common_args(gen)
    gen.add_argument("--no-clean", action="store_true", help="Do not delete data-dir first")
    gen.set_defaults(func=cmd_generate)

    analyze = sub.add_parser("analyze", help="Run analysis-only benchmark in this process")
    analyze.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    analyze.add_argument(
        "--analysis-hours",
        default="auto",
        help="Analysis lookback hours or 'auto' (default: auto)",
    )
    analyze.add_argument("--rss-limit-mb", type=float, default=0.0)
    analyze.add_argument("--min-analyzed-ratio", type=float, default=MIN_ANALYZED_RATIO)
    analyze.add_argument("--explain-volatility", action="store_true")
    analyze.add_argument("--no-profile", action="store_true")
    analyze.set_defaults(func=cmd_analyze)

    run = sub.add_parser("run", help="generate (subprocess) then analyze (subprocess)")
    _add_common_args(run)
    run.add_argument("--no-clean", action="store_true")
    run.add_argument("--analysis-hours", default="auto")
    run.add_argument("--rss-limit-mb", type=float, default=0.0)
    run.add_argument("--min-analyzed-ratio", type=float, default=MIN_ANALYZED_RATIO)
    run.add_argument("--explain-volatility", action="store_true")
    run.add_argument("--no-profile", action="store_true")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
