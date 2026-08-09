"""Flush local Parquet parts to durable storage (GCS or local remote/)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from lighter_mm.storage.backend import StorageBackend

log = logging.getLogger(__name__)


class DurableSync:
    def __init__(
        self,
        backend: StorageBackend,
        *,
        run_id: str,
        gcs_prefix: str = "lighter-mm",
    ) -> None:
        self.backend = backend
        self.run_id = run_id
        self.gcs_prefix = gcs_prefix.rstrip("/")
        self._uploaded: set[str] = set()
        self.bytes_uploaded = 0

    def run_prefix(self) -> str:
        return f"{self.gcs_prefix}/runs/{self.run_id}"

    def state_key(self) -> str:
        return f"{self.run_prefix()}/state/state.json"

    def active_pointer_key(self) -> str:
        return f"{self.gcs_prefix}/state/active_run.json"

    def lock_key(self) -> str:
        return f"{self.gcs_prefix}/state/leader.lock.json"

    def public_key(self, name: str) -> str:
        # public aggregates live outside run for stable dashboard URLs
        return f"{self.gcs_prefix}/public/{name}"

    def remote_for_local(self, local_path: Path, data_root: Path) -> str:
        rel = local_path.relative_to(data_root).as_posix()
        # Normalize dataset folders: book_samples -> books
        rel = rel.replace("book_samples/", "books/", 1)
        # Inject hour=HH when filename contains YYYYMMDD_HH
        return f"{self.run_prefix()}/{rel}"

    def upload_new_parquets(self, data_root: Path) -> list[str]:
        uploaded: list[str] = []
        for path in self._iter_parquets(data_root):
            key = str(path)
            if key in self._uploaded:
                continue
            remote = self.remote_for_local(path, data_root)
            self.backend.upload_file(path, remote, content_type="application/octet-stream")
            self._uploaded.add(key)
            self.bytes_uploaded += path.stat().st_size
            uploaded.append(remote)
            log.info(
                "parquet_flushed_remote",
                extra={"event": "parquet_flushed", "path": remote, "bytes": path.stat().st_size},
            )
        return uploaded

    @staticmethod
    def _iter_parquets(data_root: Path) -> Iterable[Path]:
        for name in ("book_samples", "trades", "markouts", "aggregates"):
            root = data_root / name
            if not root.exists():
                continue
            yield from root.rglob("*.parquet")
