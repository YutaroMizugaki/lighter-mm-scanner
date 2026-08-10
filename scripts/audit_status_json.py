"""Parse public status JSON for post-deploy smoke checks."""

from __future__ import annotations

import json
import sys
from typing import Any


def parse_status_json(raw: str | None) -> dict[str, Any]:
    """Return explicit parse outcome for shell smoke helpers."""
    if not raw:
        return {
            "present": False,
            "valid": False,
            "status": "",
            "git_sha": "",
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "present": True,
            "valid": False,
            "status": "",
            "git_sha": "",
        }
    return {
        "present": True,
        "valid": True,
        "status": str(data.get("status") or ""),
        "git_sha": str(data.get("git_sha") or ""),
    }


def main() -> None:
    raw = sys.stdin.read()
    json.dump(parse_status_json(raw), sys.stdout)


if __name__ == "__main__":
    main()
