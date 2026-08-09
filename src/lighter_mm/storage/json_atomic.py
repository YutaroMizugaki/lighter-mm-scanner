"""Atomic JSON write helpers — temp file, validate parse, rename."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def dumps_validated_json(payload: dict[str, Any], *, indent: int = 2) -> str:
    """Serialize JSON and verify the payload round-trips through json.loads."""
    data = json.dumps(payload, indent=indent, default=str)
    json.loads(data)
    return data


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically via a sibling .tmp file on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically with parse validation before publish."""
    atomic_write_text(path, dumps_validated_json(payload))
