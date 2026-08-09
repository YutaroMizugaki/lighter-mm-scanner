"""Storage backend abstraction for local vs GCS persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VersionedJson:
    """JSON payload paired with the object generation read at download time."""

    payload: dict[str, Any] | None
    generation: int | None


class StorageBackend(ABC):
    @property
    def supports_atomic_cas(self) -> bool:
        """True when compare_and_swap_json is a real distributed CAS (GCS)."""
        return False

    @abstractmethod
    def local_data_dir(self) -> Path:
        """Writable local directory for hot Parquet / sqlite."""

    @abstractmethod
    def upload_file(
        self,
        local_path: Path,
        remote_key: str,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> str:
        """Upload a file; returns remote URI.

        When ``if_generation_match`` is 0, the upload must fail if the remote
        object already exists (immutable Parquet parts).
        """

    @abstractmethod
    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        """Upload JSON object; returns remote URI."""

    @abstractmethod
    def download_json(self, remote_key: str) -> dict[str, Any] | None:
        """Download JSON if present."""

    def download_json_with_generation(self, remote_key: str) -> VersionedJson:
        """Download JSON and the generation observed at read time."""
        payload = self.download_json(remote_key)
        if payload is None:
            return VersionedJson(None, None)
        return VersionedJson(payload, 1)

    @abstractmethod
    def download_bytes(self, remote_key: str) -> bytes | None:
        pass

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]:
        pass

    @abstractmethod
    def exists(self, remote_key: str) -> bool:
        pass

    @abstractmethod
    def uri_for(self, remote_key: str) -> str:
        pass

    def compare_and_swap_json(
        self,
        remote_key: str,
        payload: dict[str, Any],
        *,
        if_generation_match: int,
    ) -> bool:
        """Best-effort CAS for local dev backends (upload + verify)."""
        self.upload_json(remote_key, payload)
        verify = self.download_json(remote_key)
        return bool(verify == payload)
