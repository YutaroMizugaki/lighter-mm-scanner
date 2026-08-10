"""GCP runtime E2E verification CLI."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from lighter_mm.runtime_verify.gcp_fetch import _gcs_cat, fetch_runtime_snapshot
from lighter_mm.runtime_verify.render import render_human, render_json
from lighter_mm.runtime_verify.sha_resolve import resolve_expected_sha
from lighter_mm.runtime_verify.verifier import verify_runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GCP runtime E2E verification (read-only default)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="asia-northeast1")
    parser.add_argument("--gcs-bucket")
    parser.add_argument("--gcs-public-bucket")
    parser.add_argument("--gcs-prefix", default="lighter-mm")
    parser.add_argument("--public-prefix", default="lighter-mm/public")
    parser.add_argument("--worker-pool", default="lighter-mm-collector")
    parser.add_argument("--analyzer-job", default="lighter-mm-analyzer")
    parser.add_argument("--dashboard-url")
    parser.add_argument("--commit-sha")
    parser.add_argument("--from-trigger")
    parser.add_argument("--trigger-region", default="global")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--execute-analyzer", action="store_true")
    parser.add_argument("--snapshot-file", help="Load snapshot JSON for tests (skips GCP fetch)")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    expected_sha, sha_source = resolve_expected_sha(
        commit_sha=args.commit_sha,
        project_id=args.project,
        trigger_name=args.from_trigger,
        trigger_region=args.trigger_region,
        repo_root=repo_root,
    )

    if args.execute_analyzer:
        print("ACTION MODE: executing analyzer job")
        subprocess.run(
            [
                "gcloud",
                "run",
                "jobs",
                "execute",
                args.analyzer_job,
                f"--project={args.project}",
                f"--region={args.region}",
                "--wait",
            ],
            check=False,
        )
        if expected_sha and args.gcs_public_bucket:
            _poll_analysis_status(
                args.gcs_public_bucket,
                args.public_prefix,
                expected_sha,
                timeout_s=600,
            )

    if args.snapshot_file:
        import json

        from lighter_mm.runtime_verify.models import RuntimeSnapshot

        raw = json.loads(Path(args.snapshot_file).read_text(encoding="utf-8"))
        snap = RuntimeSnapshot(**raw)
    else:
        snap = fetch_runtime_snapshot(
            project_id=args.project,
            region=args.region,
            gcs_bucket=args.gcs_bucket,
            gcs_public_bucket=args.gcs_public_bucket,
            worker_pool=args.worker_pool,
            analyzer_job=args.analyzer_job,
            public_prefix=args.public_prefix,
            gcs_prefix=args.gcs_prefix,
            expected_git_sha=expected_sha,
            expected_sha_source=sha_source,
            dashboard_url=args.dashboard_url,
        )

    report = verify_runtime(snap)
    if args.as_json:
        print(render_json(report))
    else:
        print(render_human(report))
    return report.exit_code()


def _poll_analysis_status(
    bucket: str,
    prefix: str,
    expected_sha: str,
    timeout_s: int = 600,
) -> None:
    path = f"{prefix.rstrip('/')}/analysis_status.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = _gcs_cat(bucket, path)
        if raw:
            import json

            try:
                data = json.loads(raw)
                if str(data.get("git_sha") or "") == expected_sha:
                    print(f"analysis_status updated git_sha={expected_sha}")
                    return
            except json.JSONDecodeError:
                pass
        time.sleep(15)
    print("WARN: analysis_status did not update within timeout")


if __name__ == "__main__":
    sys.exit(main())
