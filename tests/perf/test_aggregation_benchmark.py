"""Perf guard: aggregation benchmark smoke test (scaled-down dataset)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_benchmark_script_completes() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_aggregation.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "elapsed_s=" in proc.stdout
