"""Storage backend abstraction for local vs GCS persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageBackend(ABC):
    @abstractmethod
    def local_data_dir(self) -> Path:
        """Writable local directory for hot Parquet / sqlite."""

    @abstractmethod
    def upload_file(self, local_path: Path, remote_key: str, *, content_type: str | None = None) -> str:
        """Upload a file; returns remote URI."""

    @abstractmethod
    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        """Upload JSON object; returns remote URI."""

    @abstractmethod
    def download_json(self, remote_key: str) -> dict[str, Any] | None:
        """Download JSON if present."""

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
        if_generation_match: int | None = None,
    ) -> bool:
        """Atomic JSON upload when supported; default falls back to upload + verify."""
        self.upload_json(remote_key, payload)
        verify = self.download_json(remote_key)
        return bool(verify == payload)
