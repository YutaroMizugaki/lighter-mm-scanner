"""Probe a local Parquet book file."""

from __future__ import annotations

from pathlib import Path

from lighter_mm.runtime_verify.models import ParquetBookInfo


def probe_book_parquet(path: Path) -> ParquetBookInfo:
    info = ParquetBookInfo(object_path=str(path), size_bytes=path.stat().st_size)
    if info.size_bytes <= 0:
        info.error = "zero bytes"
        return info
    try:
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        info.row_count = pf.metadata.num_rows
        if info.row_count <= 0:
            info.error = "row_count=0"
            return info
        schema = pf.schema_arrow
        if "timestamp_ms" not in schema.names:
            info.error = "missing timestamp_ms column"
            return info
        table = pf.read(columns=["timestamp_ms"])
        col = table.column("timestamp_ms")
        if len(col) == 0:
            info.error = "no timestamp rows"
            return info
        info.max_timestamp_ms = int(col[-1].as_py())
        info.valid = True
    except Exception as exc:  # noqa: BLE001
        info.error = str(exc)
    return info
