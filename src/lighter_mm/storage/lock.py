"""GCS/local leader lock to prevent dual collectors on the same run."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lighter_mm.storage.backend import StorageBackend

log = logging.getLogger(__name__)


@dataclass
class LockInfo:
    holder_id: str
    run_id: str
    expires_at: str
    git_sha: str | None = None


class LeaderLock:
    """Lease lock stored as JSON via StorageBackend.

    Uses GCS generation preconditions when available (compare-and-swap).
    """

    def __init__(
        self,
        backend: StorageBackend,
        lock_key: str,
        *,
        holder_id: str | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self.backend = backend
        self.lock_key = lock_key
        self.holder_id = holder_id or uuid.uuid4().hex
        self.lease_seconds = lease_seconds
        self._generation: int | None = None

    def _lock_payload(self, run_id: str, git_sha: str | None) -> dict:
        info = LockInfo(
            holder_id=self.holder_id,
            run_id=run_id,
            expires_at=(datetime.now(UTC) + timedelta(seconds=self.lease_seconds)).isoformat(),
            git_sha=git_sha,
        )
        return info.__dict__

    def acquire(self, run_id: str, git_sha: str | None = None) -> bool:
        now = datetime.now(UTC)
        existing = self.backend.download_json(self.lock_key)
        if existing:
            exp = datetime.fromisoformat(existing["expires_at"])
            if existing.get("holder_id") != self.holder_id and exp > now:
                log.warning(
                    "leader lock held by %s until %s",
                    existing.get("holder_id"),
                    existing.get("expires_at"),
                    extra={"event": "leader_lock_busy"},
                )
                return False
        payload = self._lock_payload(run_id, git_sha)
        gen_match = 0 if not existing else None
        ok = self.backend.compare_and_swap_json(
            self.lock_key, payload, if_generation_match=gen_match
        )
        if not ok:
            # Fallback: non-atomic path for local backend
            self.backend.upload_json(self.lock_key, payload)
            verify = self.backend.download_json(self.lock_key)
            ok = bool(verify and verify.get("holder_id") == self.holder_id)
        if ok:
            log.info(
                "leader lock acquired",
                extra={"event": "leader_lock_acquired", "run_id": run_id},
            )
        return ok

    def renew(self, run_id: str, git_sha: str | None = None) -> bool:
        existing = self.backend.download_json(self.lock_key)
        if not existing or existing.get("holder_id") != self.holder_id:
            return self.acquire(run_id, git_sha=git_sha)
        payload = self._lock_payload(run_id, git_sha)
        ok = self.backend.compare_and_swap_json(self.lock_key, payload)
        if not ok:
            self.backend.upload_json(self.lock_key, payload)
            verify = self.backend.download_json(self.lock_key)
            ok = bool(verify and verify.get("holder_id") == self.holder_id)
        return ok

    def release(self) -> None:
        existing = self.backend.download_json(self.lock_key)
        if existing and existing.get("holder_id") == self.holder_id:
            existing["expires_at"] = datetime.now(UTC).isoformat()
            existing["released"] = True
            self.backend.compare_and_swap_json(self.lock_key, existing)
            self.backend.upload_json(self.lock_key, existing)
            log.info("leader lock released", extra={"event": "leader_lock_released"})


class LocalFileLock(LeaderLock):
    """Uses the same JSON API; LocalStorageBackend stores under remote/."""

    pass


def wait_for_leadership(
    lock: LeaderLock,
    run_id: str,
    *,
    git_sha: str | None = None,
    timeout_s: float = 60.0,
    poll_s: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if lock.acquire(run_id, git_sha=git_sha):
            return True
        time.sleep(poll_s)
    return False
