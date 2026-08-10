"""Resolve expected Git SHA for runtime verification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def resolve_expected_sha(
    *,
    commit_sha: str | None,
    project_id: str | None,
    trigger_name: str | None,
    trigger_region: str = "global",
    repo_root: Path | None = None,
) -> tuple[str | None, str]:
    if commit_sha:
        return commit_sha.strip(), "commit-sha flag"
    if trigger_name and project_id:
        sha, source = _sha_from_trigger_build(project_id, trigger_name, trigger_region)
        if sha:
            return sha, source
        return None, f"trigger {trigger_name}: no successful build SHA"
    if repo_root is None:
        repo_root = Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        sha = proc.stdout.strip()
        if sha:
            return sha, "git rev-parse HEAD"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None, "unknown"


def _sha_from_trigger_build(
    project_id: str, trigger_name: str, trigger_region: str
) -> tuple[str | None, str]:
    try:
        trigger_json = subprocess.run(
            [
                "gcloud",
                "builds",
                "triggers",
                "describe",
                trigger_name,
                f"--project={project_id}",
                f"--region={trigger_region}",
                "--format=json",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        trigger = json.loads(trigger_json)
        trigger_id = trigger.get("id") or trigger.get("name")
        builds_json = subprocess.run(
            [
                "gcloud",
                "builds",
                "list",
                f"--project={project_id}",
                f"--filter=buildTriggerId={trigger_id} AND status=SUCCESS",
                "--sort-by=~createTime",
                "--limit=1",
                "--format=json",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        builds = json.loads(builds_json or "[]")
        if not builds:
            return None, ""
        build = builds[0]
        subs = build.get("substitutions") or {}
        sha = subs.get("COMMIT_SHA") or subs.get("_COMMIT_SHA")
        if sha:
            return sha, f"trigger {trigger_name} latest SUCCESS"
        prov = build.get("sourceProvenance") or {}
        resolved = prov.get("resolvedRepoSource") or {}
        commit = resolved.get("commitSha")
        if commit:
            return commit, f"trigger {trigger_name} source provenance"
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        pass
    return None, ""
