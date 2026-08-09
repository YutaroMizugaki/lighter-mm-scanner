#!/usr/bin/env bash
# Fast post-deploy smoke checks — verify deployed revision and public status JSON.
# Intended to run at the end of cloudbuild.yaml without blocking for long analyzer runs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gcp_audit_lib.sh
source "${SCRIPT_DIR}/gcp_audit_lib.sh"

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${COMMIT_SHA:?COMMIT_SHA is required}"

REGION="${_REGION:-asia-northeast1}"
AR_REPO="${_AR_REPO:-lighter-mm}"
WORKER_POOL="${_WORKER_POOL:-lighter-mm-collector}"
ANALYZER_JOB="${_ANALYZER_JOB:-lighter-mm-analyzer}"
GCS_PUBLIC_BUCKET="${_GCS_PUBLIC_BUCKET:-}"
PUBLIC_PREFIX="${GCS_PUBLIC_PREFIX:-lighter-mm/public}"

audit_reset_counters

audit_section "Post-deploy smoke"
audit_check_worker_pool_deployed "${PROJECT_ID}" "${COMMIT_SHA}" || true
audit_check_analyzer_job_deployed "${PROJECT_ID}" "${COMMIT_SHA}" || true
audit_check_scheduler_target "${PROJECT_ID}" || true
if [[ -n "${GCS_PUBLIC_BUCKET}" ]]; then
  audit_check_public_collector_status "${GCS_PUBLIC_BUCKET}" "${PUBLIC_PREFIX}" || true
  audit_check_public_analysis_status "${GCS_PUBLIC_BUCKET}" "${PUBLIC_PREFIX}" || true
else
  audit_warn "_GCS_PUBLIC_BUCKET not set; skipping public JSON smoke"
fi

if audit_print_summary; then
  echo "POST-DEPLOY SMOKE PASSED"
  exit 0
fi

echo "POST-DEPLOY SMOKE FAILED"
exit 1
