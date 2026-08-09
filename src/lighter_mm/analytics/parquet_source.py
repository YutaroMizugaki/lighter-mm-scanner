"""Parquet source discovery and DuckDB view helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class AnalysisSources:
    """Parquet source directories for DuckDB aggregation."""

    books: Path
    trades: Path
    markouts: Path


def _default_sources(data_dir: Path) -> AnalysisSources:
    return AnalysisSources(
        books=data_dir / "book_samples",
        trades=data_dir / "trades",
        markouts=data_dir / "markouts",
    )


def _connect(
    data_dir: Path,
    *,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute(f"SET threads TO {threads or 2}")
    con.execute(f"SET memory_limit='{memory_limit or '512MB'}'")
    return con


def _glob_patterns(path: Path) -> list[str]:
    """Locate Parquet parts under hive partitions."""
    patterns: list[str] = []
    if list(path.glob("date=*/hour=*/*.parquet")):
        patterns.append(str(path / "date=*/hour=*/*.parquet"))
    if list(path.glob("date=*/*.parquet")):
        patterns.append(str(path / "date=*/*.parquet"))
    return patterns


def _glob_or_none(path: Path) -> str | None:
    patterns = _glob_patterns(path)
    return patterns[0] if patterns else None


def _parquet_list(patterns: list[str]) -> str:
    return "[" + ", ".join("'" + p.replace("'", "''") + "'" for p in patterns) + "]"


def _probe_parquet_columns(
    con: duckdb.DuckDBPyConnection, patterns: list[str]
) -> set[str]:
    if not patterns:
        return set()
    listed = _parquet_list(patterns)
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({listed}, hive_partitioning=1, union_by_name=true)"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _book_projection(available: set[str]) -> str:
    depth25 = (
        "two_sided_depth_25bps_usd"
        if "two_sided_depth_25bps_usd" in available
        else "CAST(NULL AS DOUBLE) AS two_sided_depth_25bps_usd"
    )
    is_usable = (
        "is_usable"
        if "is_usable" in available
        else "CAST(NULL AS BOOLEAN) AS is_usable"
    )
    is_inactive = (
        "is_inactive"
        if "is_inactive" in available
        else "CAST(NULL AS BOOLEAN) AS is_inactive"
    )
    book_update_age = (
        "book_update_age_ms"
        if "book_update_age_ms" in available
        else "CAST(NULL AS BIGINT) AS book_update_age_ms"
    )
    best_bid = (
        "best_bid"
        if "best_bid" in available
        else "CAST(NULL AS DOUBLE) AS best_bid"
    )
    best_ask = (
        "best_ask"
        if "best_ask" in available
        else "CAST(NULL AS DOUBLE) AS best_ask"
    )
    return f"""
        timestamp_ms, market_id, symbol, is_stale, {is_usable}, {is_inactive},
        {book_update_age}, spread_bps, mid, {best_bid}, {best_ask},
        best_bid_size_usd, best_ask_size_usd,
        two_sided_depth_5bps_usd, two_sided_depth_10bps_usd,
        {depth25},
        current_funding_rate, funding_rate, open_interest, daily_quote_token_volume
    """


def _read_view(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    patterns: list[str],
    columns: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    """Create a DuckDB view over parquet with time window + column projection."""
    if not patterns:
        return False
    listed = _parquet_list(patterns)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT {columns}
        FROM read_parquet({listed}, hive_partitioning=1, union_by_name=true)
        WHERE timestamp_ms >= {start_ms}
          AND timestamp_ms <= {end_ms}
        """
    )
    return True
