#!/usr/bin/env bash
# Read-only GCP environment audit for lighter-mm-scanner Cloud Build deploy.
# Run from Cloud Shell before triggering Cloud Build to avoid whack-a-mole failures.
#
# Usage:
#   bash scripts/gcp_doctor.sh --project PROJECT_ID
# Post-deploy runtime E2E (separate script):
#   bash scripts/gcp_runtime_verify.sh --project PROJECT_ID --from-trigger lighter-mm-main
#
#   bash scripts/gcp_doctor.sh --project PROJECT_ID \
#     --region asia-northeast1 \
#     --ar-repo lighter-mm \
#     --gcs-bucket PROJECT-lighter-mm \
#     --gcs-public-bucket PROJECT-lighter-mm-public \
#     --service-account lighter-mm-collector@PROJECT.iam.gserviceaccount.com \
#     --scheduler-service-account lighter-mm-scheduler@PROJECT.iam.gserviceaccount.com
#
# Values can also be read from a Cloud Build trigger (lighter-mm-main) when gcloud is
# configured and --from-trigger is passed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gcp_audit_lib.sh
source "${SCRIPT_DIR}/gcp_audit_lib.sh"

usage() {
  cat <<'EOF'
Usage: gcp_doctor.sh [options]

Options:
  --project PROJECT_ID              GCP project (default: gcloud config)
  --region REGION                   _REGION (default: asia-northeast1)
  --ar-repo NAME                    _AR_REPO (default: lighter-mm)
  --gcs-bucket NAME                 _GCS_BUCKET (required for full audit)
  --gcs-public-bucket NAME          _GCS_PUBLIC_BUCKET (required for full audit)
  --service-account EMAIL           _SERVICE_ACCOUNT (required for full audit)
  --scheduler-service-account EMAIL _SCHEDULER_SERVICE_ACCOUNT (optional)
  --worker-pool NAME                _WORKER_POOL (default: lighter-mm-collector)
  --analyzer-job NAME               _ANALYZER_JOB (default: lighter-mm-analyzer)
  --from-trigger NAME               Load substitutions from Cloud Build trigger
  --trigger-region REGION           Region for trigger lookup (default: global)
  -h, --help                        Show this help

Exit code 0 = READY, 1 = NOT READY or gcloud missing.
EOF
}

PROJECT_ID=""
FROM_TRIGGER=""
TRIGGER_REGION="global"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --ar-repo) AR_REPO="$2"; shift 2 ;;
    --gcs-bucket) GCS_BUCKET="$2"; shift 2 ;;
    --gcs-public-bucket) GCS_PUBLIC_BUCKET="$2"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    --scheduler-service-account) SCHEDULER_SA="$2"; shift 2 ;;
    --worker-pool) WORKER_POOL="$2"; shift 2 ;;
    --analyzer-job) ANALYZER_JOB="$2"; shift 2 ;;
    --from-trigger) FROM_TRIGGER="$2"; shift 2 ;;
    --trigger-region) TRIGGER_REGION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

REGION="${REGION:-asia-northeast1}"
AR_REPO="${AR_REPO:-lighter-mm}"
WORKER_POOL="${WORKER_POOL:-lighter-mm-collector}"
ANALYZER_JOB="${ANALYZER_JOB:-lighter-mm-analyzer}"

audit_reset_counters

if ! audit_require_gcloud; then
  audit_print_summary
  exit 1
fi

if ! PROJECT_ID="$(audit_resolve_project_id "${PROJECT_ID}")"; then
  audit_print_summary
  exit 1
fi

if [[ -n "${FROM_TRIGGER}" ]]; then
  echo "Loading substitutions from trigger ${FROM_TRIGGER}..."
  trigger_json="$(gcloud builds triggers describe "${FROM_TRIGGER}" \
    --project="${PROJECT_ID}" \
    --region="${TRIGGER_REGION}" \
    --format=json 2>/dev/null || true)"
  if [[ -z "${trigger_json}" ]]; then
    audit_fail "Cloud Build trigger '${FROM_TRIGGER}' not found in ${TRIGGER_REGION}"
  else
    _sub() {
      local key="$1"
      printf '%s' "${trigger_json}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
subs = data.get('substitutions') or {}
print(subs.get('${key}', ''), end='')
" 2>/dev/null || true
    }
    REGION="${REGION:-$(_sub _REGION)}"
    AR_REPO="${AR_REPO:-$(_sub _AR_REPO)}"
    GCS_BUCKET="${GCS_BUCKET:-$(_sub _GCS_BUCKET)}"
    GCS_PUBLIC_BUCKET="${GCS_PUBLIC_BUCKET:-$(_sub _GCS_PUBLIC_BUCKET)}"
    SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-$(_sub _SERVICE_ACCOUNT)}"
    SCHEDULER_SA="${SCHEDULER_SA:-$(_sub _SCHEDULER_SERVICE_ACCOUNT)}"
    WORKER_POOL="${WORKER_POOL:-$(_sub _WORKER_POOL)}"
    ANALYZER_JOB="${ANALYZER_JOB:-$(_sub _ANALYZER_JOB)}"
    audit_pass "loaded trigger ${FROM_TRIGGER}"
  fi
fi

echo "GCP doctor for project ${PROJECT_ID} (read-only)"

audit_check_substitutions
audit_check_apis "${PROJECT_ID}"
audit_check_artifact_registry "${PROJECT_ID}"
audit_check_gcs_buckets "${PROJECT_ID}"
audit_check_service_accounts "${PROJECT_ID}"
audit_check_cloud_build_iam "${PROJECT_ID}"
audit_check_service_account_user_on_runtime "${PROJECT_ID}"
audit_check_worker_pool_prereqs "${PROJECT_ID}"
audit_check_scheduler_deploy_prereqs "${PROJECT_ID}"

if audit_print_summary; then
  echo ""
  echo "SAFE TO RETRY CLOUD BUILD"
  exit 0
fi

echo ""
echo "DO NOT RETRY YET — fix failures above first."
audit_print_failure_help
exit 1
