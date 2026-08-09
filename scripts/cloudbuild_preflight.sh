#!/usr/bin/env bash
# Preflight checks for Cloud Build deploy — no serviceusage.services.enable required.
# Requires Cloud Build substitutions mapped to env vars (options.automapSubstitutions and/or step env).
set -euo pipefail

fail() {
  echo "PREFLIGHT FAILED: $*" >&2
  exit 1
}

fail_api_not_enabled() {
  local api="$1"
  local label="$2"
  echo "PREFLIGHT FAILED:" >&2
  echo "${label} (${api}) is not enabled." >&2
  echo "" >&2
  echo "Run once as a project administrator:" >&2
  echo "" >&2
  echo "gcloud services enable ${api} \\" >&2
  echo "  --project=\"\${PROJECT_ID}\"" >&2
  echo "" >&2
  echo "Or enable all required APIs:" >&2
  echo "" >&2
  echo "bash scripts/bootstrap_gcp.sh \"\${PROJECT_ID}\"" >&2
  echo "" >&2
  echo "After the API is enabled, retry the Cloud Build." >&2
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

[[ -n "${SCHEDULER_SA}" ]] || fail "_SCHEDULER_SERVICE_ACCOUNT or _SERVICE_ACCOUNT is required"

echo "Checking Artifact Registry repository ${AR_REPO} in ${REGION}..."
if ! gcloud artifacts repositories describe "${AR_REPO}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" >/dev/null 2>&1; then
  fail "Artifact Registry repository '${AR_REPO}' not found in ${REGION}"
fi

echo "Checking private GCS bucket gs://${GCS_BUCKET}..."
if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  fail "GCS bucket gs://${GCS_BUCKET} not found or not accessible"
fi

echo "Checking public GCS bucket gs://${GCS_PUBLIC_BUCKET}..."
if ! gcloud storage buckets describe "gs://${GCS_PUBLIC_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  fail "GCS bucket gs://${GCS_PUBLIC_BUCKET} not found or not accessible"
fi

echo "Checking service account ${SERVICE_ACCOUNT}..."
if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  fail "Service account '${SERVICE_ACCOUNT}' not found"
fi

echo "Checking scheduler service account ${SCHEDULER_SA}..."
if ! gcloud iam service-accounts describe "${SCHEDULER_SA}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  fail "Scheduler service account '${SCHEDULER_SA}' not found"
fi

check_api_enabled() {
  local api="$1"
  local label="$2"
  if gcloud services list --enabled --project="${PROJECT_ID}" --filter="config.name:${api}" --format="value(config.name)" 2>/dev/null | grep -qx "${api}"; then
    echo "  ${label}: enabled"
    return 0
  fi
  # Fallback: probe API without enable permission
  case "${api}" in
    run.googleapis.com)
      if gcloud run worker-pools list --project="${PROJECT_ID}" --region="${REGION}" --limit=1 >/dev/null 2>&1; then
        echo "  ${label}: available (probe ok)"
        return 0
      fi
      ;;
    cloudscheduler.googleapis.com)
      if gcloud scheduler jobs list --project="${PROJECT_ID}" --location="${REGION}" --limit=1 >/dev/null 2>&1; then
        echo "  ${label}: available (probe ok)"
        return 0
      fi
      ;;
  esac
  fail_api_not_enabled "${api}" "${label}"
}

echo "Checking required APIs (read-only)..."
check_api_enabled "run.googleapis.com" "Cloud Run API"
check_api_enabled "cloudscheduler.googleapis.com" "Cloud Scheduler API"

echo "Preflight passed."
