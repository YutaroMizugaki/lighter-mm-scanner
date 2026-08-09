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
        public_bucket_name: str | None = None,
    ) -> None:
        from google.cloud import storage  # lazy import

        self.bucket_name = bucket_name
        self.public_bucket_name = (public_bucket_name or "").strip() or None
        self.local_root = local_root
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.make_public_prefix = make_public_prefix
        self._client = storage.Client(project=project_id)
        self._bucket = self._client.bucket(bucket_name)
        self._public_bucket = (
            self._client.bucket(self.public_bucket_name) if self.public_bucket_name else None
        )
        log.info(
            "gcs_backend_ready private=%s public=%s",
            self.bucket_name,
            self.public_bucket_name or "",
            extra={
                "event": "gcs_backend_ready",
                "path": self.public_bucket_name or "",
            },
        )

    def local_data_dir(self) -> Path:
        return self.local_root

    def upload_file(self, local_path: Path, remote_key: str, *, content_type: str | None = None) -> str:
        blob = self._bucket.blob(remote_key)
        blob.upload_from_filename(str(local_path), content_type=content_type)
        # Raw parquet / files stay on the private bucket only.
        uri = self.uri_for(remote_key)
        log.info("gcs_uploaded %s", uri, extra={"event": "gcs_uploaded", "path": remote_key})
        return uri

    # Public dashboard JSON must not sit behind the GCS default 1h edge cache —
    # otherwise Vercel keeps serving a frozen latest.json while the live object
    # on the public bucket has already advanced (observed: markets=0 for ~1h).
    _PUBLIC_CACHE_CONTROL = "public, max-age=60, must-revalidate"
    # State / leader lock are also fetched via HTTPS for ops; avoid 1h defaults.
    _PRIVATE_JSON_CACHE_CONTROL = "private, max-age=0, must-revalidate"

    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        data = json.dumps(payload, indent=2, default=str)
        blob = self._bucket.blob(remote_key)
        want_public = public or (
            self.make_public_prefix is not None and remote_key.startswith(self.make_public_prefix)
        )
        blob.cache_control = (
            self._PUBLIC_CACHE_CONTROL if want_public else self._PRIVATE_JSON_CACHE_CONTROL
        )
        blob.upload_from_string(data, content_type="application/json")

        if want_public:
            if self._public_bucket is not None:
                try:
                    pub = self._public_bucket.blob(remote_key)
                    pub.cache_control = self._PUBLIC_CACHE_CONTROL
                    pub.upload_from_string(data, content_type="application/json")
                    log.info(
                        "gcs_public_uploaded %s",
                        f"gs://{self.public_bucket_name}/{remote_key}",
                        extra={"event": "gcs_public_uploaded", "path": remote_key},
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "gcs_public_upload_failed %s (%s)",
                        remote_key,
                        exc,
                        extra={"event": "gcs_public_upload_failed", "path": remote_key},
                    )
                    raise
            else:
                # Fail closed: Vercel reads the public bucket. Silent best-effort
                # make_public on the private blob looks healthy while the dashboard
                # stays empty under uniform bucket-level access.
                log.error(
                    "gcs_public_bucket_missing; refusing public upload of %s",
                    remote_key,
                    extra={"event": "gcs_public_bucket_missing", "path": remote_key},
                )
                raise RuntimeError(
                    "GCS_PUBLIC_BUCKET is required for public dashboard JSON uploads"
                )

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
        bucket = self.public_bucket_name or self.bucket_name
        return f"https://storage.googleapis.com/{bucket}/{remote_key}"

    @staticmethod
    def _try_make_public(blob: Any) -> None:
        try:
            blob.make_public()
        except Exception as exc:  # noqa: BLE001 — uniform bucket-level access may block ACL
            log.warning(
                "make_public skipped for %s (%s); set GCS_PUBLIC_BUCKET instead",
                blob.name,
                exc,
            )
