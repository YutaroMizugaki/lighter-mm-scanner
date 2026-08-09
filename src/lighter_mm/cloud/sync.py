"""Flush local Parquet parts to durable storage (GCS or local remote/)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from lighter_mm.storage.backend import StorageBackend

log = logging.getLogger(__name__)

# Local hot-path dataset names ↔ durable remote names.
LOCAL_TO_REMOTE = {
    "book_samples": "books",
    "trades": "trades",
    "markouts": "markouts",
    "aggregates": "aggregates",
}
REMOTE_TO_LOCAL = {remote: local for local, remote in LOCAL_TO_REMOTE.items()}


class DurableSync:
    def __init__(
        self,
        backend: StorageBackend,
        *,
        run_id: str,
        gcs_prefix: str = "lighter-mm",
        public_prefix: str | None = None,
    ) -> None:
        self.backend = backend
        self.run_id = run_id
        self.gcs_prefix = gcs_prefix.rstrip("/")
        self.public_prefix = (public_prefix or f"{self.gcs_prefix}/public").rstrip("/")
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
        return f"{self.public_prefix}/{name.lstrip('/')}"

    def remote_for_local(self, local_path: Path, data_root: Path) -> str:
        rel = local_path.relative_to(data_root).as_posix()
        for local_name, remote_name in LOCAL_TO_REMOTE.items():
            prefix = f"{local_name}/"
            if rel.startswith(prefix):
                rel = f"{remote_name}/" + rel[len(prefix) :]
                break
        return f"{self.run_prefix()}/{rel}"

    def local_for_remote(self, remote_key: str, data_root: Path) -> Path | None:
        prefix = f"{self.run_prefix()}/"
        if not remote_key.startswith(prefix):
            return None
        rel = remote_key[len(prefix) :]
        for remote_name, local_name in REMOTE_TO_LOCAL.items():
            remote_prefix = f"{remote_name}/"
            if rel.startswith(remote_prefix):
                return data_root / local_name / rel[len(remote_prefix) :]
        return None

    def upload_new_parquets(
        self, data_root: Path, paths: Iterable[Path] | None = None
    ) -> list[str]:
        """Upload closed Parquet parts.

        Prefer an explicit ``paths`` list from ``ParquetStore.take_closed_paths()``
        so in-progress writers opened after rotation are never uploaded mid-write.
        When ``paths`` is omitted, falls back to scanning the tree (CLI / hydrate).
        """
        uploaded: list[str] = []
        candidates = list(paths) if paths is not None else list(self._iter_parquets(data_root))
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
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

    def hydrate_run_parquets(
        self,
        data_root: Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        progress_every: int = 25,
    ) -> list[str]:
        """Download durable Parquet for this run into the local hot path.

        Required after Cloud Run restarts because ``/tmp`` is ephemeral while
        analysis still reads local ``book_samples/`` (mapped from remote ``books/``).

        ``on_progress`` is invoked periodically (and once at the end) so the
        caller can renew the leader lock during long resumes.
        """
        restored: list[str] = []
        scanned = 0
        prefix = f"{self.run_prefix()}/"
        for remote_key in self.backend.list_keys(prefix):
            if not remote_key.endswith(".parquet"):
                continue
            scanned += 1
            local_path = self.local_for_remote(remote_key, data_root)
            if local_path is None:
                continue
            local_key = str(local_path)
            if local_path.exists():
                self._uploaded.add(local_key)
            else:
                raw = self.backend.download_bytes(remote_key)
                if raw is None:
                    continue
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(raw)
                self._uploaded.add(local_key)
                restored.append(remote_key)
                log.info(
                    "parquet_hydrated_local",
                    extra={"event": "parquet_hydrated", "path": remote_key, "bytes": len(raw)},
                )
            if on_progress is not None and progress_every > 0 and scanned % progress_every == 0:
                on_progress(scanned, len(restored))
        if on_progress is not None:
            on_progress(scanned, len(restored))
        return restored

    @staticmethod
    def _iter_parquets(data_root: Path) -> Iterable[Path]:
        for name in ("book_samples", "trades", "markouts", "aggregates"):
            root = data_root / name
            if not root.exists():
                continue
            yield from root.rglob("*.parquet")
