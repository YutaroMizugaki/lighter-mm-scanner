"""Schema-tolerant helpers for GCP post-deploy audit (Cloud Run JSON, image digests)."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_IMAGE_DIGEST_RE = re.compile(r"@([^@]+)$")


def _dig_into(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _containers_from_describe(data: dict[str, Any]) -> list[dict[str, Any]]:
    paths: tuple[tuple[str, ...], ...] = (
        ("spec", "template", "spec", "containers"),
        ("template", "template", "containers"),
        ("template", "containers"),
        ("spec", "template", "containers"),
    )
    for path in paths:
        val = _dig_into(data, *path)
        if isinstance(val, list) and val:
            return [c for c in val if isinstance(c, dict)]
    return []


def extract_container_image(describe_json: dict[str, Any]) -> str:
    """Return the first container image from a Cloud Run describe JSON payload."""
    containers = _containers_from_describe(describe_json)
    if not containers:
        return ""
    image = containers[0].get("image")
    return image if isinstance(image, str) else ""


def extract_git_sha_env(describe_json: dict[str, Any]) -> str:
    """Return GIT_SHA from the first container env block, if present."""
    containers = _containers_from_describe(describe_json)
    if not containers:
        return ""
    env = containers[0].get("env")
    if not isinstance(env, list):
        return ""
    for item in env:
        if isinstance(item, dict) and item.get("name") == "GIT_SHA":
            val = item.get("value")
            return val if isinstance(val, str) else ""
    return ""


def image_digest(image_ref: str) -> str:
    """Extract digest suffix from an image reference (e.g. ...@sha256:abc)."""
    if not image_ref:
        return ""
    match = _IMAGE_DIGEST_RE.search(image_ref)
    return match.group(1) if match else ""


def normalize_digest(digest: str) -> str:
    if not digest:
        return ""
    return digest.removeprefix("sha256:")


def digests_match(deployed_digest: str, expected_digest: str) -> bool:
    if not deployed_digest or not expected_digest:
        return False
    return normalize_digest(deployed_digest) == normalize_digest(expected_digest)


def image_tag_matches_commit(image_ref: str, commit_sha: str) -> bool:
    if not image_ref or not commit_sha:
        return False
    return f":{commit_sha}" in image_ref or f"/collector:{commit_sha}" in image_ref


def deployment_provenance_check(
    *,
    deployed_image: str,
    commit_sha: str,
    ar_digest: str = "",
    git_sha_env: str = "",
) -> tuple[bool, str]:
    """Return (ok, reason) for deployment image provenance.

  When both Artifact Registry and deployed image digests are available, they must match.
  GIT_SHA env is supplementary and cannot override a digest mismatch.
    """
    if not deployed_image:
        return False, "empty_image"
    deployed_d = image_digest(deployed_image)
    if ar_digest and deployed_d:
        if digests_match(deployed_d, ar_digest):
            return True, "digest_match"
        return False, "digest_mismatch"
    if image_tag_matches_commit(deployed_image, commit_sha):
        if git_sha_env and git_sha_env == commit_sha:
            return True, "tag_match_git_sha_confirmed"
        return True, "tag_match"
    if git_sha_env and git_sha_env == commit_sha:
        return True, "git_sha_env"
    return False, "no_match"


def deployment_provenance_ok(
    *,
    deployed_image: str,
    commit_sha: str,
    ar_digest: str = "",
    git_sha_env: str = "",
) -> bool:
    """Return True when deployed image digest/tag/env matches the expected commit."""
    ok, _ = deployment_provenance_check(
        deployed_image=deployed_image,
        commit_sha=commit_sha,
        ar_digest=ar_digest,
        git_sha_env=git_sha_env,
    )
    return ok


def extract_trigger_service_account(trigger_json: dict[str, Any]) -> str:
    """Return explicit serviceAccount from a Cloud Build trigger describe JSON."""
    for key in ("serviceAccount", "serviceAccountEmail"):
        val = trigger_json.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return 2
    cmd = args[0]

    if cmd == "image":
        data = json.load(sys.stdin)
        print(extract_container_image(data), end="")
        return 0
    if cmd == "git-sha":
        data = json.load(sys.stdin)
        print(extract_git_sha_env(data), end="")
        return 0
    if cmd == "digest" and len(args) >= 2:
        print(image_digest(args[1]), end="")
        return 0
    if cmd == "digest-match" and len(args) >= 3:
        print("yes" if digests_match(args[1], args[2]) else "no", end="")
        return 0
    if cmd == "provenance-ok" and len(args) >= 3:
        deployed_image = args[1]
        commit_sha = args[2]
        ar_digest = args[3] if len(args) > 3 else ""
        git_sha_env = args[4] if len(args) > 4 else ""
        ok, _ = deployment_provenance_check(
            deployed_image=deployed_image,
            commit_sha=commit_sha,
            ar_digest=ar_digest,
            git_sha_env=git_sha_env,
        )
        print("yes" if ok else "no", end="")
        return 0
    if cmd == "provenance-reason" and len(args) >= 3:
        deployed_image = args[1]
        commit_sha = args[2]
        ar_digest = args[3] if len(args) > 3 else ""
        git_sha_env = args[4] if len(args) > 4 else ""
        ok, reason = deployment_provenance_check(
            deployed_image=deployed_image,
            commit_sha=commit_sha,
            ar_digest=ar_digest,
            git_sha_env=git_sha_env,
        )
        print(reason if ok else f"fail:{reason}", end="")
        return 0
    if cmd == "trigger-sa":
        data = json.load(sys.stdin)
        print(extract_trigger_service_account(data), end="")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
