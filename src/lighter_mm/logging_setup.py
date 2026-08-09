"""Structured logging for local stdout and Cloud Logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", None),
        }
        for key in (
            "run_id",
            "git_sha",
            "collector_version",
            "market_id",
            "symbol",
            "path",
            "bytes",
            "detail",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Drop nulls for cleaner logs
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)


def setup_logging(*, structured: bool = False, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if structured:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, event: str, msg: str = "", **fields: Any) -> None:
    logger.info(msg or event, extra={"event": event, **fields})
