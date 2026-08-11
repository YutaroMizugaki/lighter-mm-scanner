#!/usr/bin/env python3
"""Real Parquet A/B benchmark — Legacy vs two-stage Analyzer (read-only)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import duckdb

from lighter_mm.analytics.aggregation import analyze_range
from lighter_mm.analytics.parquet_source import _default_sources
from lighter_mm.analytics.real_benchmark import (
    RESULT_FAIL,
    build_run_snapshot,
    compare_snapshots,
    format_console_summary,
    peak_rss_mb,
)
from lighter_mm.config import Settings

DEFAULT_OUTPUT_DIR = Path("/tmp/lighter-mm-real-benchmark")
SCRIPT_PATH = Path(__file__).resolve()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A/B benchmark Legacy vs two-stage Analyzer on real Parquet (read-only).",
    )
    parser.add_argument("--data-dir", type=Path, required=True, help="Parquet data directory")
    parser.add_argument("--hours", type=float, default=None, help="Analysis window hours")
    parser.add_argument("--start-ms", type=int, default=None)
    parser.add_argument("--end-ms", type=int, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for legacy.json / two_stage.json / comparison.json",
    )
    parser.add_argument("--stage2-top-n", type=int, default=None)
    parser.add_argument("--stage1-min-coverage", type=float, default=None)
    parser.add_argument("--stage1-min-trades", type=int, default=None)
    parser.add_argument("--stage1-min-spread-bps", type=float, default=None)
    parser.add_argument("--duckdb-memory-limit", type=str, default=None)
    parser.add_argument("--duckdb-threads", type=int, default=None)
    parser.add_argument(
        "--max-two-stage-rss-mb",
        type=float,
        default=2800.0,
        help="Warn when two-stage peak RSS exceeds this (MB)",
    )
    parser.add_argument(
        "--min-rss-reduction-pct",
        type=float,
        default=30.0,
        help="Target peak RSS reduction %% (warning if below)",
    )
    parser.add_argument(
        "--min-elapsed-reduction-pct",
        type=float,
        default=20.0,
        help="Target elapsed reduction %% (warning if below)",
    )
    parser.add_argument(
        "--include-paper-mm",
        action="store_true",
        help="Enable Paper MM in both runs (default: disabled)",
    )
    parser.add_argument(
        "--order",
        choices=("legacy-first", "two-stage-first"),
        default="legacy-first",
        help="Child execution order (cache bias may apply)",
    )
    parser.add_argument(
        "--child",
        action="store_true",
        help="Run single analyzer mode and write JSON (internal)",
    )
    parser.add_argument(
        "--mode",
        choices=("legacy", "two-stage"),
        default=None,
        help="Analyzer mode for --child",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path for --child",
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=1e-9,
        help="Float comparison absolute tolerance",
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=1e-6,
        help="Float comparison relative tolerance",
    )
    return parser.parse_args(argv)


def _validate_window_args(args: argparse.Namespace) -> None:
    has_hours = args.hours is not None
    has_explicit = args.start_ms is not None and args.end_ms is not None
    if has_hours and has_explicit:
        raise SystemExit("use either --hours or --start-ms/--end-ms, not both")
    if not has_hours and not has_explicit:
        raise SystemExit("provide --hours or both --start-ms and --end-ms")


def resolve_window(args: argparse.Namespace) -> tuple[int, int]:
    _validate_window_args(args)
    if args.start_ms is not None and args.end_ms is not None:
        return args.start_ms, args.end_ms
    con = duckdb.connect(database=":memory:")
    end_ms = int(con.execute("SELECT CAST(epoch_ms(current_timestamp) AS BIGINT)").fetchone()[0])
    start_ms = end_ms - int(float(args.hours) * 3600 * 1000)
    return start_ms, end_ms


def build_settings(args: argparse.Namespace, mode: str) -> Settings:
    base = Settings(data_dir=args.data_dir)
    updates: dict[str, object] = {
        "analyzer_two_stage_enabled": mode == "two-stage",
        "paper_mm_enabled": args.include_paper_mm,
    }
    if args.stage2_top_n is not None:
        updates["analyzer_stage2_top_n"] = args.stage2_top_n
    if args.stage1_min_coverage is not None:
        updates["analyzer_stage1_min_coverage"] = args.stage1_min_coverage
    if args.stage1_min_trades is not None:
        updates["analyzer_stage1_min_trades"] = args.stage1_min_trades
    if args.stage1_min_spread_bps is not None:
        updates["analyzer_stage1_min_spread_bps"] = args.stage1_min_spread_bps
    if args.duckdb_memory_limit is not None:
        updates["duckdb_memory_limit"] = args.duckdb_memory_limit
    if args.duckdb_threads is not None:
        updates["duckdb_threads"] = args.duckdb_threads
    return base.model_copy(update=updates)


def run_child(args: argparse.Namespace) -> int:
    if args.mode is None:
        raise SystemExit("--child requires --mode legacy|two-stage")
    if args.output is None:
        raise SystemExit("--child requires --output")
    start_ms, end_ms = resolve_window(args)
    settings = build_settings(args, args.mode)
    sources = _default_sources(args.data_dir)
    mem_limit = settings.duckdb_memory_limit
    threads = settings.duckdb_threads

    t0 = time.monotonic()
    result = analyze_range(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        sources=sources,
        benchmark_profile=True,
        duckdb_memory_limit=mem_limit,
        duckdb_threads=threads,
        read_only=True,
    )
    elapsed = time.monotonic() - t0
    rss = peak_rss_mb()
    snapshot = build_run_snapshot(
        result,
        mode=args.mode,
        start_ms=start_ms,
        end_ms=end_ms,
        elapsed_seconds=elapsed,
        peak_rss_mb_value=rss,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, default=str))
    if snapshot.get("error"):
        print(f"analyzer error: {snapshot['error']}", file=sys.stderr)
        return 1
    return 0


def _child_cmd(
    args: argparse.Namespace,
    mode: str,
    output: Path,
    start_ms: int,
    end_ms: int,
) -> list[str]:
    cmd = [
        sys.executable,
        str(SCRIPT_PATH),
        "--child",
        "--mode",
        mode,
        "--data-dir",
        str(args.data_dir),
        "--start-ms",
        str(start_ms),
        "--end-ms",
        str(end_ms),
        "--output",
        str(output),
    ]
    if args.stage2_top_n is not None:
        cmd.extend(["--stage2-top-n", str(args.stage2_top_n)])
    if args.stage1_min_coverage is not None:
        cmd.extend(["--stage1-min-coverage", str(args.stage1_min_coverage)])
    if args.stage1_min_trades is not None:
        cmd.extend(["--stage1-min-trades", str(args.stage1_min_trades)])
    if args.stage1_min_spread_bps is not None:
        cmd.extend(["--stage1-min-spread-bps", str(args.stage1_min_spread_bps)])
    if args.duckdb_memory_limit is not None:
        cmd.extend(["--duckdb-memory-limit", args.duckdb_memory_limit])
    if args.duckdb_threads is not None:
        cmd.extend(["--duckdb-threads", str(args.duckdb_threads)])
    if args.include_paper_mm:
        cmd.append("--include-paper-mm")
    return cmd


def run_parent(args: argparse.Namespace) -> int:
    start_ms, end_ms = resolve_window(args)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = out_dir / "legacy.json"
    two_stage_path = out_dir / "two_stage.json"
    comparison_path = out_dir / "comparison.json"

    print(
        "Note: Legacy and two-stage run in separate subprocesses for accurate peak RSS.\n"
        "Filesystem/page-cache bias may favor the second run; use --order to swap.\n"
    )

    modes_order = (
        ("legacy", legacy_path),
        ("two-stage", two_stage_path),
    )
    if args.order == "two-stage-first":
        modes_order = list(reversed(modes_order))

    exit_codes: dict[str, int] = {}
    for mode, path in modes_order:
        print(f"Running {mode} analyzer subprocess...")
        proc = subprocess.run(
            _child_cmd(args, mode, path, start_ms, end_ms),
            check=False,
        )
        exit_codes[mode] = proc.returncode
        if proc.returncode != 0:
            print(f"{mode} subprocess failed with exit code {proc.returncode}", file=sys.stderr)

    if not legacy_path.exists() or not two_stage_path.exists():
        print("missing child output JSON", file=sys.stderr)
        return 1

    legacy = json.loads(legacy_path.read_text())
    two_stage = json.loads(two_stage_path.read_text())

    comparison = compare_snapshots(
        legacy,
        two_stage,
        abs_tol=args.abs_tol,
        rel_tol=args.rel_tol,
        min_rss_reduction_pct=args.min_rss_reduction_pct,
        min_elapsed_reduction_pct=args.min_elapsed_reduction_pct,
        max_two_stage_rss_mb=args.max_two_stage_rss_mb,
    )
    comparison_path.write_text(json.dumps(comparison, indent=2, default=str))
    print()
    print(format_console_summary(comparison))
    print()
    print(f"Wrote {legacy_path}")
    print(f"Wrote {two_stage_path}")
    print(f"Wrote {comparison_path}")

    if any(code != 0 for code in exit_codes.values()):
        return 1
    if comparison.get("result") == RESULT_FAIL:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.child:
        return run_child(args)
    return run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
