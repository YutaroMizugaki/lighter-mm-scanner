"""Parquet integrity validation, discovery, quarantine, and temp-file cleanup."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

log = logging.getLogger(__name__)

# Temp / partial suffixes never read by analysis.
_SKIP_SUFFIXES = (".tmp", ".partial", ".incomplete")
_STALE_TEMP_PATTERNS = ("*.tmp", "*.partial", "*.incomplete")


def is_analysis_candidate(path: Path) -> bool:
    """Return True if path looks like a finalized Parquet part (not a temp file)."""
    name = path.name
    if not name.endswith(".parquet"):
        return False
    for suffix in _SKIP_SUFFIXES:
        if name.endswith(suffix):
            return False
    return True


def discover_parquet_files(root: Path) -> list[Path]:
    """List finalized Parquet parts under hive partitions, excluding temp files."""
    if not root.is_dir():
        return []
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in ("date=*/hour=*/*.parquet", "date=*/*.parquet"):
        for path in root.glob(pattern):
            resolved = path.resolve()
            if resolved in seen:
                continue
            if is_analysis_candidate(path):
                seen.add(resolved)
                files.append(path)
    return sorted(files)


def validate_parquet_file(path: Path, *, data_probe: bool = True) -> tuple[bool, str | None]:
    """Validate Parquet footer/metadata and optionally probe the first row group."""
    try:
        if not path.exists():
            return False, "file does not exist"
        if path.stat().st_size == 0:
            return False, "empty file (0 bytes)"
        pf = pq.ParquetFile(path)
        _ = pf.metadata
        _ = pf.schema_arrow
        if not data_probe:
            return True, None
        if pf.metadata.num_row_groups > 0:
            pf.read_row_group(0)
        elif pf.metadata.num_rows > 0:
            pf.read(columns=[pf.schema_arrow.names[0]])
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def parquet_max_timestamp_ms(path: Path) -> int | None:
    """Return max timestamp_ms using row-group statistics, with a light fallback."""
    try:
        pf = pq.ParquetFile(path)
        schema = pf.schema_arrow
        if "timestamp_ms" not in schema.names:
            return None
        col_idx = schema.get_field_index("timestamp_ms")
        max_ts: int | None = None
        for rg_idx in range(pf.metadata.num_row_groups):
            rg = pf.metadata.row_group(rg_idx)
            if col_idx >= rg.num_columns:
                continue
            stats = rg.column(col_idx).statistics
            if stats is not None and stats.has_max:
                candidate = int(stats.max)
                max_ts = candidate if max_ts is None else max(max_ts, candidate)
        if max_ts is not None:
            return max_ts
        if pf.metadata.num_row_groups == 0:
            return None
        table = pf.read_row_group(0, columns=["timestamp_ms"])
        if table.num_rows == 0:
            return None
        values = [int(v) for v in table.column("timestamp_ms").to_pylist() if v is not None]
        return max(values) if values else None
    except Exception as exc:  # noqa: BLE001
        log.debug("parquet_max_timestamp_ms failed path=%s error=%s", path, exc)
        return None


def partition_parquet_files(
    files: list[Path],
) -> tuple[list[Path], list[dict[str, str]]]:
    """Split candidate files into valid Parquet parts and corrupt/skipped entries."""
    valid: list[Path] = []
    corrupt: list[dict[str, str]] = []
    for path in files:
        ok, err = validate_parquet_file(path)
        if ok:
            valid.append(path)
        else:
            corrupt.append({"path": str(path), "error": err or "unknown"})
            log.warning("corrupted parquet skipped path=%s error=%s", path, err)
    return valid, corrupt


def quarantine_corrupt_file(path: Path, dataset_root: Path) -> Path | None:
    """Move a corrupt file under dataset_root/quarantine/ preserving relative layout."""
    if not path.exists():
        return None
    try:
        rel = path.relative_to(dataset_root)
    except ValueError:
        rel = Path(path.name)
    dest = dataset_root / "quarantine" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest)
    log.info("quarantined corrupt parquet src=%s dest=%s", path, dest)
    return dest


def cleanup_stale_temp_files(
    root: Path,
    *,
    max_age_seconds: float = 3600.0,
) -> list[Path]:
    """Remove old *.tmp / *.partial / *.incomplete files under root."""
    removed: list[Path] = []
    if not root.is_dir():
        return removed
    now = time.time()
    for pattern in _STALE_TEMP_PATTERNS:
        for path in root.rglob(pattern):
            try:
                age = now - path.stat().st_mtime
                if age >= max_age_seconds:
                    path.unlink()
                    removed.append(path)
                    log.info(
                        "removed stale temp file path=%s age_seconds=%.0f",
                        path,
                        age,
                    )
            except OSError as exc:
                log.warning("failed to remove temp file %s: %s", path, exc)
    return removed


def prepare_parquet_dataset(
    dataset_root: Path,
    *,
    quarantine: bool = True,
    cleanup_temp: bool = True,
    temp_max_age_seconds: float = 3600.0,
) -> tuple[list[Path], list[dict[str, str]]]:
    """
    Discover, cleanup temp files, validate, and optionally quarantine corrupt parts.
    """
    if cleanup_temp:
        cleanup_stale_temp_files(dataset_root, max_age_seconds=temp_max_age_seconds)
    candidates = discover_parquet_files(dataset_root)
    valid, corrupt = partition_parquet_files(candidates)
    if quarantine and corrupt:
        for entry in corrupt:
            try:
                quarantine_corrupt_file(Path(entry["path"]), dataset_root)
            except OSError as exc:
                log.warning(
                    "failed to quarantine corrupt parquet path=%s error=%s",
                    entry["path"],
                    exc,
                )
    return valid, corrupt


def parquet_health_summary(
    valid_counts: dict[str, int],
    corrupt_entries: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a parquet health block for analysis results."""
    total_valid = sum(valid_counts.values())
    total_corrupt = len(corrupt_entries)
    if total_valid == 0 and total_corrupt > 0:
        status = "failed"
    elif total_corrupt > 0:
        status = "degraded"
    else:
        status = "healthy"
    return {
        "status": status,
        "valid_parquet_files": total_valid,
        "corrupt_parquet_files": total_corrupt,
        "valid_by_dataset": valid_counts,
        "skipped_files": corrupt_entries,
    }
