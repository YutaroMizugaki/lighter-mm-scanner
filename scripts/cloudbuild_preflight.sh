#!/usr/bin/env bash
# Preflight checks for Cloud Build deploy — read-only, no API enable, no IAM changes.
# Requires Cloud Build substitutions mapped to env vars (automapSubstitutions + step env).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gcp_audit_lib.sh
source "${SCRIPT_DIR}/gcp_audit_lib.sh"

fail() {
  echo "PREFLIGHT FAILED: $*" >&2
  audit_print_failure_help
  exit 1
}

: "${PROJECT_ID:?PROJECT_ID is required (set via Cloud Build substitution / automapSubstitutions)}"
: "${_REGION:?_REGION is required}"
: "${_AR_REPO:?_AR_REPO is required}"
: "${_GCS_BUCKET:?_GCS_BUCKET is required}"
: "${_GCS_PUBLIC_BUCKET:?_GCS_PUBLIC_BUCKET is required}"
: "${_SERVICE_ACCOUNT:?_SERVICE_ACCOUNT is required}"

REGION="${_REGION}"
AR_REPO="${_AR_REPO}"
GCS_BUCKET="${_GCS_BUCKET}"
GCS_PUBLIC_BUCKET="${_GCS_PUBLIC_BUCKET}"
SERVICE_ACCOUNT="${_SERVICE_ACCOUNT}"
SCHEDULER_SA="${_SCHEDULER_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT}}"
WORKER_POOL="${_WORKER_POOL:-lighter-mm-collector}"
ANALYZER_JOB="${_ANALYZER_JOB:-lighter-mm-analyzer}"

[[ -n "${SCHEDULER_SA}" ]] || fail "_SCHEDULER_SERVICE_ACCOUNT or _SERVICE_ACCOUNT is required"

audit_reset_counters

echo "Cloud Build preflight (read-only) for project ${PROJECT_ID}..."
audit_require_gcloud || fail "gcloud not available in preflight image"

audit_check_substitutions
audit_check_apis "${PROJECT_ID}"
audit_check_artifact_registry "${PROJECT_ID}"
audit_check_gcs_buckets "${PROJECT_ID}"
audit_check_service_accounts "${PROJECT_ID}"
audit_check_cloud_build_iam "${PROJECT_ID}"
audit_check_service_account_user_on_runtime "${PROJECT_ID}"
audit_check_worker_pool_prereqs "${PROJECT_ID}"
audit_check_scheduler_deploy_prereqs "${PROJECT_ID}"

if [[ "${AUDIT_FAIL_COUNT}" -gt 0 ]]; then
  echo "" >&2
  echo "PREFLIGHT FAILED: ${AUDIT_FAIL_COUNT} blocking issue(s) found." >&2
  audit_print_failure_help
  exit 1
fi

echo "Preflight passed (${AUDIT_WARN_COUNT} warning(s), ${AUDIT_UNKNOWN_COUNT} unknown)."
