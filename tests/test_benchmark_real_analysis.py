"""Tests for scripts/benchmark_real_analysis.py helpers and parent flow."""

from __future__ import annotations

import importlib.util
import json
import time
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from lighter_mm.analytics.real_benchmark import RESULT_PASS
from lighter_mm.storage.state import MarketLifecycleEntry, RunState
from tests.test_collector_analyzer_split import _write_book_parquet

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_real_analysis.py"
_spec = importlib.util.spec_from_file_location("benchmark_real_analysis", SCRIPT)
_benchmark = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_benchmark)


def _ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _write_state(data_dir: Path, state: RunState) -> None:
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(state.model_dump_json())


def test_resolve_window_uses_state_watermark_not_current_time(tmp_path: Path) -> None:
    durable = _ms("2026-01-01T00:15:00+00:00")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_state(
        data_dir,
        RunState(
            run_id="r1",
            started_at="2025-12-31T00:00:00+00:00",
            status="running",
            last_durable_event_ms=durable,
        ),
    )
    args = Namespace(
        hours=1.0,
        start_ms=None,
        end_ms=None,
        data_dir=data_dir,
    )
    with patch.object(_benchmark.time, "time", return_value=_ms("2026-02-01T00:00:00+00:00") / 1000.0):
        start_ms, end_ms = _benchmark.resolve_window(args)
    assert end_ms == durable
    assert start_ms == durable - 3600 * 1000


def test_resolve_window_uses_parquet_watermark_without_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    book_ts = int(time.time() * 1000) - 3_600_000
    book_path = data_dir / "book_samples/date=2026-01-01/hour=00/part.parquet"
    _write_book_parquet(book_path, book_ts)
    args = Namespace(
        hours=2.0,
        start_ms=None,
        end_ms=None,
        data_dir=data_dir,
    )
    with patch.object(_benchmark.time, "time", return_value=time.time()):
        start_ms, end_ms = _benchmark.resolve_window(args)
    assert end_ms == book_ts
    assert start_ms == book_ts - 2 * 3600 * 1000


def test_market_lifecycle_passed_to_analyze_range(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lifecycle = {
        42: MarketLifecycleEntry(first_active_at_ms=1_000_000, removed_at_ms=2_000_000),
    }
    _write_state(
        data_dir,
        RunState(
            run_id="r1",
            started_at="2026-01-01T00:00:00+00:00",
            market_lifecycle=lifecycle,
        ),
    )
    output = tmp_path / "child.json"
    args = Namespace(
        mode="legacy",
        output=output,
        data_dir=data_dir,
        hours=None,
        start_ms=1_000_000,
        end_ms=2_000_000,
        stage2_top_n=None,
        stage1_min_coverage=None,
        stage1_min_trades=None,
        stage1_min_spread_bps=None,
        duckdb_memory_limit=None,
        duckdb_threads=None,
        include_paper_mm=False,
    )
    captured: dict = {}

    def _fake_analyze_range(*_a, **kwargs):
        captured.update(kwargs)
        return {
            "scored": [],
            "markets": [],
            "book_row_count": 1,
            "trade_row_count": 0,
            "markout_row_count": 0,
        }

    with patch.object(_benchmark, "analyze_range", _fake_analyze_range):
        code = _benchmark.run_child(args)
    assert code == 0
    assert captured.get("market_lifecycle") == lifecycle


def test_run_parent_skips_comparison_on_child_failure(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_comparison = {
        "result": RESULT_PASS,
        "hard_failures": [],
    }
    (out_dir / "comparison.json").write_text(json.dumps(stale_comparison))

    args = Namespace(
        data_dir=tmp_path / "data",
        output_dir=out_dir,
        hours=None,
        start_ms=1_000_000,
        end_ms=2_000_000,
        order="legacy-first",
        abs_tol=1e-9,
        rel_tol=1e-6,
        min_rss_reduction_pct=30.0,
        min_elapsed_reduction_pct=20.0,
        max_two_stage_rss_mb=2800.0,
        stage2_top_n=None,
        stage1_min_coverage=None,
        stage1_min_trades=None,
        stage1_min_spread_bps=None,
        duckdb_memory_limit=None,
        duckdb_threads=None,
        include_paper_mm=False,
    )

    with patch.object(_benchmark.subprocess, "run", return_value=type("P", (), {"returncode": 1})()):
        code = _benchmark.run_parent(args)

    assert code == 1
    assert not (out_dir / "comparison.json").exists()
