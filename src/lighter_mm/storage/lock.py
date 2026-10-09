"""GCS/local leader lock to prevent dual collectors on the same run."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lighter_mm.storage.backend import StorageBackend, VersionedJson

log = logging.getLogger(__name__)


class LostLeadershipError(Exception):
    """Raised when a process no longer holds the leader lock."""


@dataclass
class LockInfo:
    holder_id: str
    run_id: str
    expires_at: str
    git_sha: str | None = None


def _parse_lock_expiry(payload: dict | None) -> datetime | None:
    if not payload:
        return None
    raw = payload.get("expires_at")
    if not raw:
        return None
    try:
        exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp
    except (TypeError, ValueError):
        log.warning("leader lock has invalid expires_at: %r", raw)
        return None


class LeaderLock:
    """Lease lock stored as JSON via StorageBackend.

    GCS backends use generation-precondition CAS with no upload fallback.
    Local backends use best-effort compare_and_swap_json only.
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
        self.cas_conflicts = 0
        # Same-process renew (event-loop heartbeat vs to_thread sync) must not
        # CAS-conflict with itself; GCS treats that as lost leadership.
        self._op_lock = threading.Lock()

    def _lock_payload(self, run_id: str, git_sha: str | None) -> dict:
        info = LockInfo(
            holder_id=self.holder_id,
            run_id=run_id,
            expires_at=(datetime.now(UTC) + timedelta(seconds=self.lease_seconds)).isoformat(),
            git_sha=git_sha,
        )
        return info.__dict__

    def _is_held_by_other(self, current: VersionedJson) -> bool:
        if not current.payload:
            return False
        holder = current.payload.get("holder_id")
        if not holder:
            log.warning("leader lock missing holder_id; treating as expired")
            return False
        exp = _parse_lock_expiry(current.payload)
        if exp is None:
            return False
        return holder != self.holder_id and exp > datetime.now(UTC)

    def acquire(self, run_id: str, git_sha: str | None = None) -> bool:
        with self._op_lock:
            current = self.backend.download_json_with_generation(self.lock_key)
            if self._is_held_by_other(current):
                log.warning(
                    "leader lock held by %s until %s",
                    current.payload.get("holder_id") if current.payload else "?",
                    current.payload.get("expires_at") if current.payload else "?",
                    extra={"event": "leader_lock_busy"},
                )
                return False
            gen_match = 0 if current.payload is None else int(current.generation or 0)
            payload = self._lock_payload(run_id, git_sha)
            ok = self.backend.compare_and_swap_json(
                self.lock_key, payload, if_generation_match=gen_match
            )
            if not ok:
                self.cas_conflicts += 1
                log.debug(
                    "leader lock CAS conflict on acquire",
                    extra={"event": "leader_lock_cas_conflict", "run_id": run_id},
                )
            if ok:
                log.info(
                    "leader lock acquired",
                    extra={"event": "leader_lock_acquired", "run_id": run_id},
                )
            return ok

    def renew(self, run_id: str, git_sha: str | None = None) -> bool:
        with self._op_lock:
            current = self.backend.download_json_with_generation(self.lock_key)
            if not current.payload or current.payload.get("holder_id") != self.holder_id:
                return False
            if current.payload.get("released"):
                return False
            if current.generation is None:
                return False
            payload = self._lock_payload(run_id, git_sha)
            ok = self.backend.compare_and_swap_json(
                self.lock_key, payload, if_generation_match=int(current.generation)
            )
            if not ok:
                self.cas_conflicts += 1
                log.debug(
                    "leader lock CAS conflict on renew",
                    extra={"event": "leader_lock_cas_conflict", "run_id": run_id},
                )
            return ok

    def release(self) -> None:
        with self._op_lock:
            current = self.backend.download_json_with_generation(self.lock_key)
            if not current.payload or current.payload.get("holder_id") != self.holder_id:
                return
            if current.generation is None:
                return
            existing = dict(current.payload)
            existing["expires_at"] = datetime.now(UTC).isoformat()
            existing["released"] = True
            if self.backend.compare_and_swap_json(
                self.lock_key, existing, if_generation_match=int(current.generation)
            ):
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
