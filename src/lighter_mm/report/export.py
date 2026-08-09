"""CSV / table export helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from lighter_mm.analytics.aggregation import scored_to_records
from lighter_mm.scoring import ScoredMarket


def export_csv(scored: list[ScoredMarket], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = scored_to_records(scored)
    if not records:
        pl.DataFrame({"message": ["no data"]}).write_csv(path)
        return path
    pl.DataFrame(records).write_csv(path)
    return path


def export_records(records: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(records).write_csv(path)
    return path
