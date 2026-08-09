"""Local filesystem backend (dev / LOCAL mode)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lighter_mm.storage.backend import StorageBackend


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "remote").mkdir(parents=True, exist_ok=True)

    def local_data_dir(self) -> Path:
        return self.root

    def _path(self, remote_key: str) -> Path:
        path = self.root / "remote" / remote_key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def upload_file(self, local_path: Path, remote_key: str, *, content_type: str | None = None) -> str:
        dest = self._path(remote_key)
        shutil.copy2(local_path, dest)
        return self.uri_for(remote_key)

    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        dest = self._path(remote_key)
        dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return self.uri_for(remote_key)

    def download_json(self, remote_key: str) -> dict[str, Any] | None:
        path = self.root / "remote" / remote_key
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def download_bytes(self, remote_key: str) -> bytes | None:
        path = self.root / "remote" / remote_key
        if not path.exists():
            return None
        return path.read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        base = self.root / "remote"
        out: list[str] = []
        if not base.exists():
            return out
        for p in base.rglob("*"):
            if p.is_file():
                key = str(p.relative_to(base)).replace("\\", "/")
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)

    def exists(self, remote_key: str) -> bool:
        return (self.root / "remote" / remote_key).exists()

    def uri_for(self, remote_key: str) -> str:
        return f"file://{(self.root / 'remote' / remote_key).resolve()}"
