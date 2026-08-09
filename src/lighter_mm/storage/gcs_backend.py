"""Google Cloud Storage backend (keyless ADC inside GCP)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lighter_mm.storage.backend import StorageBackend

log = logging.getLogger(__name__)


class GCSStorageBackend(StorageBackend):
    def __init__(
        self,
        bucket_name: str,
        *,
        local_root: Path,
        project_id: str | None = None,
        make_public_prefix: str | None = "lighter-mm/public/",
    ) -> None:
        from google.cloud import storage  # lazy import

        self.bucket_name = bucket_name
        self.local_root = local_root
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.make_public_prefix = make_public_prefix
        self._client = storage.Client(project=project_id)
        self._bucket = self._client.bucket(bucket_name)

    def local_data_dir(self) -> Path:
        return self.local_root

    def upload_file(self, local_path: Path, remote_key: str, *, content_type: str | None = None) -> str:
        blob = self._bucket.blob(remote_key)
        blob.upload_from_filename(str(local_path), content_type=content_type)
        self._maybe_public(blob, remote_key)
        uri = self.uri_for(remote_key)
        log.info("gcs_uploaded %s", uri, extra={"event": "gcs_uploaded", "path": remote_key})
        return uri

    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        blob = self._bucket.blob(remote_key)
        data = json.dumps(payload, indent=2, default=str)
        blob.upload_from_string(data, content_type="application/json")
        if public or (self.make_public_prefix and remote_key.startswith(self.make_public_prefix)):
            self._try_make_public(blob)
        uri = self.uri_for(remote_key)
        log.info("gcs_uploaded %s", uri, extra={"event": "gcs_uploaded", "path": remote_key})
        return uri

    def download_json(self, remote_key: str) -> dict[str, Any] | None:
        blob = self._bucket.blob(remote_key)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())

    def download_bytes(self, remote_key: str) -> bytes | None:
        blob = self._bucket.blob(remote_key)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        return [b.name for b in self._client.list_blobs(self.bucket_name, prefix=prefix)]

    def exists(self, remote_key: str) -> bool:
        return self._bucket.blob(remote_key).exists()

    def uri_for(self, remote_key: str) -> str:
        return f"gs://{self.bucket_name}/{remote_key}"

    def public_https_url(self, remote_key: str) -> str:
        return f"https://storage.googleapis.com/{self.bucket_name}/{remote_key}"

    def _maybe_public(self, blob: Any, remote_key: str) -> None:
        if self.make_public_prefix and remote_key.startswith(self.make_public_prefix):
            self._try_make_public(blob)

    @staticmethod
    def _try_make_public(blob: Any) -> None:
        try:
            blob.make_public()
        except Exception as exc:  # noqa: BLE001 — uniform bucket-level access may block ACL
            log.warning(
                "make_public skipped for %s (%s); use IAM on public prefix/bucket instead",
                blob.name,
                exc,
            )
