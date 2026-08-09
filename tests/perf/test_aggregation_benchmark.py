"""Perf guard: aggregation benchmark smoke test (scaled-down dataset)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _parse_ratio(stdout: str) -> float:
    for line in stdout.splitlines():
        if line.startswith("analyzed_ratio="):
            return float(line.split("=", 1)[1])
    raise AssertionError("analyzed_ratio missing from benchmark output")


def test_benchmark_script_completes() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_aggregation.py"
    data_dir = Path("/tmp/lighter-mm-bench-pytest")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "run",
            "--data-dir",
            str(data_dir),
            "--markets",
            "10",
            "--rows-per-market",
            "200",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    out = proc.stdout
    assert "generated_rows=" in out
    assert "analyzed_book_rows=" in out
    assert "analyzed_ratio=" in out
    assert "analysis_peak_rss_mb=" in out
    assert "analysis_elapsed_s=" in out
    assert _parse_ratio(out) >= 0.95
