"""Local filesystem backend (dev / LOCAL mode)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lighter_mm.storage.backend import StorageBackend, VersionedJson
from lighter_mm.storage.json_atomic import atomic_write_json


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

    def _gen_path(self, remote_key: str) -> Path:
        return Path(str(self._path(remote_key)) + ".gen")

    def upload_file(
        self,
        local_path: Path,
        remote_key: str,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> str:
        dest = self._path(remote_key)
        if if_generation_match == 0 and dest.exists():
            raise FileExistsError(f"remote object already exists: {remote_key}")
        shutil.copy2(local_path, dest)
        return self.uri_for(remote_key)

    def upload_json(self, remote_key: str, payload: dict[str, Any], *, public: bool = False) -> str:
        dest = self._path(remote_key)
        atomic_write_json(dest, payload)
        gen_path = self._gen_path(remote_key)
        gen = int(gen_path.read_text()) if gen_path.exists() else 0
        gen_path.write_text(str(gen + 1), encoding="utf-8")
        return self.uri_for(remote_key)

    def download_json(self, remote_key: str) -> dict[str, Any] | None:
        return self.download_json_with_generation(remote_key).payload

    def download_json_with_generation(self, remote_key: str) -> VersionedJson:
        path = self.root / "remote" / remote_key
        if not path.exists():
            return VersionedJson(None, None)
        gen_path = self._gen_path(remote_key)
        generation = int(gen_path.read_text()) if gen_path.exists() else 1
        return VersionedJson(json.loads(path.read_text(encoding="utf-8")), generation)

    def compare_and_swap_json(
        self,
        remote_key: str,
        payload: dict[str, Any],
        *,
        if_generation_match: int,
    ) -> bool:
        current = self.download_json_with_generation(remote_key)
        if if_generation_match == 0:
            if current.payload is not None:
                return False
        elif current.generation != if_generation_match:
            return False
        dest = self._path(remote_key)
        atomic_write_json(dest, payload)
        new_gen = (current.generation or 0) + 1
        self._gen_path(remote_key).write_text(str(new_gen), encoding="utf-8")
        verify = self.download_json(remote_key)
        return bool(verify == payload)

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
            if p.is_file() and not p.name.endswith(".gen"):
                key = str(p.relative_to(base)).replace("\\", "/")
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)

    def exists(self, remote_key: str) -> bool:
        return (self.root / "remote" / remote_key).exists()

    def uri_for(self, remote_key: str) -> str:
        return f"file://{(self.root / 'remote' / remote_key).resolve()}"
