"""Flush local Parquet parts to durable storage (GCS or local remote/)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from lighter_mm.storage.backend import StorageBackend
from lighter_mm.storage.parquet_validation import validate_parquet_file

log = logging.getLogger(__name__)

# Local hot-path dataset names ↔ durable remote names.
LOCAL_TO_REMOTE = {
    "book_samples": "books",
    "trades": "trades",
    "markouts": "markouts",
    "aggregates": "aggregates",
}
REMOTE_TO_LOCAL = {remote: local for local, remote in LOCAL_TO_REMOTE.items()}


@dataclass
class UploadedParquet:
    """Result of a durable Parquet upload — used to decide local deletion."""

    local_path: Path
    remote_key: str
    size_bytes: int


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

    def analyzer_lock_key(self) -> str:
        return f"{self.gcs_prefix}/state/analyzer.lock.json"

    def analysis_request_key(self, run_id: str) -> str:
        return f"{self.gcs_prefix}/analysis-requests/{run_id}.json"

    def final_analysis_marker_key(self) -> str:
        return f"{self.run_prefix()}/analysis/final.json"

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
        self,
        data_root: Path,
        paths: Iterable[Path] | None = None,
        *,
        delete_local_on_success: bool = True,
    ) -> list[UploadedParquet]:
        """Upload closed Parquet parts with immutable remote keys.

        Prefer an explicit ``paths`` list from ``ParquetStore.take_closed_paths()``
        so in-progress writers opened after rotation are never uploaded mid-write.
        When ``paths`` is omitted, falls back to scanning the tree (CLI / hydrate).

        On success, optionally deletes the local file (upload → confirm → unlink).
        Failed uploads leave local files in place for retry.
        """
        uploaded: list[UploadedParquet] = []
        candidates = list(paths) if paths is not None else list(self._iter_parquets(data_root))
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            key = str(path)
            if key in self._uploaded:
                continue
            remote = self.remote_for_local(path, data_root)
            size = path.stat().st_size
            try:
                self.backend.upload_file(
                    path,
                    remote,
                    content_type="application/octet-stream",
                    if_generation_match=0,
                )
            except FileExistsError:
                log.warning(
                    "parquet remote key already exists; skipping %s -> %s",
                    path,
                    remote,
                    extra={"event": "parquet_upload_exists", "path": remote},
                )
                # Treat as durable — remote already has this immutable key.
                if delete_local_on_success and path.exists():
                    path.unlink()
                self._uploaded.add(key)
                uploaded.append(UploadedParquet(path, remote, size))
                continue
            except Exception:
                log.warning(
                    "parquet upload failed for %s",
                    path,
                    extra={"event": "parquet_upload_failed", "path": remote},
                )
                raise
            self._uploaded.add(key)
            self.bytes_uploaded += size
            uploaded.append(UploadedParquet(path, remote, size))
            log.info(
                "parquet_flushed_remote",
                extra={"event": "parquet_flushed", "path": remote, "bytes": size},
            )
            if delete_local_on_success:
                try:
                    path.unlink()
                except OSError as exc:
                    log.warning("failed to delete local parquet %s: %s", path, exc)
        return uploaded

    def hydrate_run_parquets(
        self,
        data_root: Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        progress_every: int = 25,
    ) -> list[str]:
        """Download durable Parquet for this run into the local hot path.

        Used for local debug / manual recovery / CLI — NOT called by the cloud
        collector on resume (analysis reads GCS-mounted history directly).

        ``on_progress`` is invoked periodically (and once at the end) so the
        caller can renew locks during long hydrates.
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
                ok, err = validate_parquet_file(local_path)
                if ok:
                    self._uploaded.add(local_key)
                    continue
                log.warning(
                    "local parquet corrupt; re-downloading path=%s error=%s",
                    local_path,
                    err,
                )
                try:
                    local_path.unlink()
                except OSError:
                    pass
            raw = self.backend.download_bytes(remote_key)
            if raw is None:
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = local_path.with_suffix(".parquet.tmp")
            try:
                tmp_path.write_bytes(raw)
                ok, err = validate_parquet_file(tmp_path)
                if not ok:
                    log.warning(
                        "remote parquet corrupt; skipping hydrate path=%s error=%s",
                        remote_key,
                        err,
                    )
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    continue
                os.replace(tmp_path, local_path)
            except OSError as exc:
                log.warning("hydrate atomic write failed path=%s error=%s", remote_key, exc)
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
                continue
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
