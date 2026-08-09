#!/usr/bin/env bash
# Shared read-only GCP audit helpers for cloudbuild_preflight.sh and gcp_doctor.sh.
# Does NOT enable APIs, create resources, or modify IAM.
set -euo pipefail

AUDIT_FAIL_COUNT=0
AUDIT_UNKNOWN_COUNT=0
AUDIT_WARN_COUNT=0

audit_reset_counters() {
  AUDIT_FAIL_COUNT=0
  AUDIT_UNKNOWN_COUNT=0
  AUDIT_WARN_COUNT=0
}

audit_pass() {
  printf 'PASS %s\n' "$*"
}

audit_fail() {
  printf 'FAIL %s\n' "$*" >&2
  AUDIT_FAIL_COUNT=$((AUDIT_FAIL_COUNT + 1))
}

audit_warn() {
  printf 'WARN %s\n' "$*" >&2
  AUDIT_WARN_COUNT=$((AUDIT_WARN_COUNT + 1))
}

audit_unknown() {
  printf 'UNKNOWN %s\n' "$*"
  AUDIT_UNKNOWN_COUNT=$((AUDIT_UNKNOWN_COUNT + 1))
}

audit_section() {
  printf '\n=== %s ===\n' "$*"
}

# APIs required by bootstrap_gcp.sh and cloudbuild.yaml deploy steps.
GCP_REQUIRED_APIS=(
  "run.googleapis.com|Cloud Run API"
  "cloudscheduler.googleapis.com|Cloud Scheduler API"
  "cloudbuild.googleapis.com|Cloud Build API"
  "artifactregistry.googleapis.com|Artifact Registry API"
  "storage.googleapis.com|Cloud Storage API"
  "iam.googleapis.com|IAM API"
  "logging.googleapis.com|Cloud Logging API"
  "cloudresourcemanager.googleapis.com|Cloud Resource Manager API"
)

# Project-level roles Cloud Build SA needs for deploy + scheduler + IAM binding steps.
GCP_CLOUD_BUILD_ROLES=(
  "roles/run.admin"
  "roles/iam.serviceAccountUser"
  "roles/artifactregistry.writer"
  "roles/cloudscheduler.admin"
)

# Bucket roles runtime SA needs (collector + analyzer JSON writes).
GCP_RUNTIME_BUCKET_ROLES=(
  "roles/storage.objectAdmin"
  "roles/storage.objectUser"
)

audit_require_gcloud() {
  if ! command -v gcloud >/dev/null 2>&1; then
    audit_fail "gcloud CLI not found (install Google Cloud SDK)"
    return 1
  fi
  return 0
}

audit_resolve_project_id() {
  local project_id="${1:-}"
  if [[ -z "${project_id}" ]]; then
    project_id="$(gcloud config get-value project 2>/dev/null || true)"
  fi
  if [[ -z "${project_id}" || "${project_id}" == "(unset)" ]]; then
    audit_fail "PROJECT_ID is required (pass --project or gcloud config set project)"
    return 1
  fi
  printf '%s' "${project_id}"
}

audit_cloud_build_sa() {
  local project_id="$1"
  gcloud projects describe "${project_id}" --format='value(projectNumber)' 2>/dev/null \
    | awk '{print $1 "@cloudbuild.gserviceaccount.com"}'
}

audit_api_enabled() {
  local project_id="$1"
  local api="$2"
  local label="$3"

  if gcloud services list --enabled --project="${project_id}" \
      --filter="config.name:${api}" --format="value(config.name)" 2>/dev/null \
      | grep -qx "${api}"; then
    audit_pass "${label}"
    return 0
  fi

  # Probe without services.list permission (Cloud Build may lack serviceusage.services.list).
  case "${api}" in
    run.googleapis.com)
      if gcloud run worker-pools list --project="${project_id}" --region="${REGION:-asia-northeast1}" --limit=1 >/dev/null 2>&1; then
        audit_pass "${label} (probe ok)"
        return 0
      fi
      ;;
    cloudscheduler.googleapis.com)
      if gcloud scheduler jobs list --project="${project_id}" --location="${REGION:-asia-northeast1}" --limit=1 >/dev/null 2>&1; then
        audit_pass "${label} (probe ok)"
        return 0
      fi
      ;;
    artifactregistry.googleapis.com)
      if gcloud artifacts repositories list --project="${project_id}" --location="${REGION:-asia-northeast1}" --limit=1 >/dev/null 2>&1; then
        audit_pass "${label} (probe ok)"
        return 0
      fi
      ;;
    storage.googleapis.com)
      if gcloud storage buckets list --project="${project_id}" --limit=1 >/dev/null 2>&1; then
        audit_pass "${label} (probe ok)"
        return 0
      fi
      ;;
  esac

  audit_fail "${label} (${api}) is not enabled"
  return 1
}

audit_check_apis() {
  local project_id="$1"
  audit_section "APIs"
  for entry in "${GCP_REQUIRED_APIS[@]}"; do
    local api="${entry%%|*}"
    local label="${entry#*|}"
    audit_api_enabled "${project_id}" "${api}" "${label}" || true
  done
}

audit_check_substitution() {
  local name="$1"
  local value="${2:-}"
  if [[ -z "${value}" ]]; then
    audit_fail "Cloud Build substitution ${name} is empty"
    return 1
  fi
  audit_pass "${name}=${value}"
  return 0
}

audit_check_substitutions() {
  audit_section "Cloud Build substitutions"
  audit_check_substitution "_REGION" "${REGION:-}" || true
  audit_check_substitution "_AR_REPO" "${AR_REPO:-}" || true
  audit_check_substitution "_GCS_BUCKET" "${GCS_BUCKET:-}" || true
  audit_check_substitution "_GCS_PUBLIC_BUCKET" "${GCS_PUBLIC_BUCKET:-}" || true
  audit_check_substitution "_SERVICE_ACCOUNT" "${SERVICE_ACCOUNT:-}" || true

  if [[ -n "${WORKER_POOL:-}" ]]; then
    audit_pass "_WORKER_POOL=${WORKER_POOL}"
  else
    audit_warn "_WORKER_POOL not set (cloudbuild default: lighter-mm-collector)"
  fi
  if [[ -n "${ANALYZER_JOB:-}" ]]; then
    audit_pass "_ANALYZER_JOB=${ANALYZER_JOB}"
  else
    audit_warn "_ANALYZER_JOB not set (cloudbuild default: lighter-mm-analyzer)"
  fi
  if [[ -n "${SCHEDULER_SA:-}" ]]; then
    audit_pass "_SCHEDULER_SERVICE_ACCOUNT=${SCHEDULER_SA}"
  elif [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
    audit_warn "_SCHEDULER_SERVICE_ACCOUNT empty; will fall back to _SERVICE_ACCOUNT"
  else
    audit_fail "_SCHEDULER_SERVICE_ACCOUNT or _SERVICE_ACCOUNT required for Scheduler steps"
  fi
}

audit_check_artifact_registry() {
  local project_id="$1"
  audit_section "Artifact Registry"
  if [[ -z "${AR_REPO:-}" || -z "${REGION:-}" ]]; then
    audit_unknown "Artifact Registry (_AR_REPO / _REGION not set)"
    return 0
  fi
  if gcloud artifacts repositories describe "${AR_REPO}" \
      --project="${project_id}" \
      --location="${REGION}" >/dev/null 2>&1; then
    audit_pass "repository ${AR_REPO} in ${REGION}"
  else
    audit_fail "Artifact Registry repository '${AR_REPO}' not found in ${REGION}"
  fi
}

audit_bucket_has_member_role() {
  local bucket="$1"
  local member="$2"
  local policy
  if ! policy="$(gcloud storage buckets get-iam-policy "gs://${bucket}" --format=json 2>/dev/null)"; then
    audit_unknown "cannot read IAM policy for gs://${bucket}"
    return 1
  fi
  local found=0
  local role
  for role in "${GCP_RUNTIME_BUCKET_ROLES[@]}"; do
    if printf '%s' "${policy}" | grep -q "\"role\": \"${role}\"" \
        && printf '%s' "${policy}" | grep -q "\"${member}\""; then
      audit_pass "gs://${bucket}: ${member} has ${role}"
      found=1
      break
    fi
  done
  if [[ "${found}" -eq 0 ]]; then
    audit_fail "gs://${bucket}: ${member} missing storage write access (need objectAdmin or objectUser)"
    return 1
  fi
  return 0
}

audit_check_gcs_buckets() {
  local project_id="$1"
  audit_section "GCS"
  if [[ -z "${GCS_BUCKET:-}" ]]; then
    audit_fail "private bucket (_GCS_BUCKET) not set"
    return 0
  fi
  if gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${project_id}" >/dev/null 2>&1; then
    audit_pass "private bucket gs://${GCS_BUCKET}"
  else
    audit_fail "private bucket gs://${GCS_BUCKET} not found or not accessible"
  fi

  if [[ -z "${GCS_PUBLIC_BUCKET:-}" ]]; then
    audit_fail "public bucket (_GCS_PUBLIC_BUCKET) not set"
    return 0
  fi
  if gcloud storage buckets describe "gs://${GCS_PUBLIC_BUCKET}" --project="${project_id}" >/dev/null 2>&1; then
    audit_pass "public bucket gs://${GCS_PUBLIC_BUCKET}"
  else
    audit_fail "public bucket gs://${GCS_PUBLIC_BUCKET} not found or not accessible"
  fi

  if [[ -n "${GCS_PUBLIC_BUCKET:-}" ]]; then
    local pub_policy
    if pub_policy="$(gcloud storage buckets get-iam-policy "gs://${GCS_PUBLIC_BUCKET}" --format=json 2>/dev/null)"; then
      if printf '%s' "${pub_policy}" | grep -q 'allUsers' \
          && printf '%s' "${pub_policy}" | grep -q 'roles/storage.objectViewer'; then
        audit_pass "public bucket has allUsers:objectViewer (Vercel/dashboard GET)"
      else
        audit_warn "public bucket missing allUsers:objectViewer — dashboard may not load JSON"
      fi
    else
      audit_unknown "cannot verify public bucket IAM (need storage.buckets.getIamPolicy)"
    fi
  fi

  if [[ -n "${SERVICE_ACCOUNT:-}" && -n "${GCS_BUCKET:-}" ]]; then
    audit_bucket_has_member_role "${GCS_BUCKET}" "serviceAccount:${SERVICE_ACCOUNT}" || true
  fi
  if [[ -n "${SERVICE_ACCOUNT:-}" && -n "${GCS_PUBLIC_BUCKET:-}" ]]; then
    audit_bucket_has_member_role "${GCS_PUBLIC_BUCKET}" "serviceAccount:${SERVICE_ACCOUNT}" || true
  fi
}

audit_check_service_accounts() {
  local project_id="$1"
  audit_section "Service Accounts"
  if [[ -z "${SERVICE_ACCOUNT:-}" ]]; then
    audit_fail "runtime service account (_SERVICE_ACCOUNT) not set"
  elif gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" --project="${project_id}" >/dev/null 2>&1; then
    audit_pass "runtime SA ${SERVICE_ACCOUNT}"
  else
    audit_fail "runtime service account '${SERVICE_ACCOUNT}' not found"
  fi

  local scheduler_sa="${SCHEDULER_SA:-${SERVICE_ACCOUNT:-}}"
  if [[ -z "${scheduler_sa}" ]]; then
    audit_fail "scheduler service account not set (_SCHEDULER_SERVICE_ACCOUNT or _SERVICE_ACCOUNT)"
  elif gcloud iam service-accounts describe "${scheduler_sa}" --project="${project_id}" >/dev/null 2>&1; then
    audit_pass "scheduler SA ${scheduler_sa}"
  else
    audit_fail "scheduler service account '${scheduler_sa}' not found"
  fi
}

audit_member_has_project_role() {
  local project_id="$1"
  local member="$2"
  local required_role="$3"
  local roles
  if ! roles="$(gcloud projects get-iam-policy "${project_id}" \
      --flatten="bindings[].members" \
      --filter="bindings.members:${member}" \
      --format="value(bindings.role)" 2>/dev/null)"; then
    audit_unknown "cannot read project IAM for ${member}"
    return 1
  fi
  if printf '%s\n' "${roles}" | grep -qx "${required_role}"; then
    audit_pass "${member} has ${required_role}"
    return 0
  fi
  audit_fail "${member} missing ${required_role}"
  return 1
}

audit_check_cloud_build_iam() {
  local project_id="$1"
  audit_section "Cloud Build IAM"
  local build_sa
  if ! build_sa="$(audit_cloud_build_sa "${project_id}")" || [[ -z "${build_sa}" ]]; then
    audit_unknown "cannot resolve Cloud Build service account"
    return 0
  fi
  audit_pass "Cloud Build SA ${build_sa}"
  local role
  for role in "${GCP_CLOUD_BUILD_ROLES[@]}"; do
    audit_member_has_project_role "${project_id}" "serviceAccount:${build_sa}" "${role}" || true
  done
}

audit_check_service_account_user_on_runtime() {
  local project_id="$1"
  audit_section "Service Account User (deploy)"
  if [[ -z "${SERVICE_ACCOUNT:-}" ]]; then
    audit_unknown "runtime SA not set; cannot verify iam.serviceAccountUser"
    return 0
  fi
  local build_sa
  if ! build_sa="$(audit_cloud_build_sa "${project_id}")" || [[ -z "${build_sa}" ]]; then
    audit_unknown "cannot resolve Cloud Build SA"
    return 0
  fi
  local policy
  if ! policy="$(gcloud iam service-accounts get-iam-policy "${SERVICE_ACCOUNT}" \
      --project="${project_id}" --format=json 2>/dev/null)"; then
    audit_unknown "cannot read IAM policy on ${SERVICE_ACCOUNT} (need iam.serviceAccounts.getIamPolicy)"
    return 0
  fi
  if printf '%s' "${policy}" | grep -q "\"serviceAccount:${build_sa}\"" \
      && printf '%s' "${policy}" | grep -q 'roles/iam.serviceAccountUser'; then
    audit_pass "Cloud Build SA can act as ${SERVICE_ACCOUNT}"
  else
    # Project-level roles/iam.serviceAccountUser also satisfies deploy; already checked above.
    audit_warn "no direct SA-user binding on ${SERVICE_ACCOUNT} (ok if project-level iam.serviceAccountUser is granted)"
  fi
}

audit_check_scheduler_deploy_prereqs() {
  local project_id="$1"
  audit_section "Cloud Scheduler deploy prerequisites"
  local scheduler_sa="${SCHEDULER_SA:-${SERVICE_ACCOUNT:-}}"
  local analyzer_job="${ANALYZER_JOB:-lighter-mm-analyzer}"
  local region="${REGION:-asia-northeast1}"

  audit_pass "scheduler job name: lighter-mm-analyzer-schedule"
  audit_pass "analyzer job target: ${analyzer_job}"
  audit_pass "scheduler region: ${region}"
  audit_pass "Jobs v2 URI: https://run.googleapis.com/v2/projects/${project_id}/locations/${region}/jobs/${analyzer_job}:run"

  if [[ -n "${scheduler_sa}" ]]; then
  audit_warn "roles/run.invoker on ${analyzer_job} for ${scheduler_sa} is granted by grant-scheduler-invoker step (verify after first deploy)"
  fi

  # If analyzer job already exists, check invoker binding proactively.
  if gcloud run jobs describe "${analyzer_job}" \
      --project="${project_id}" --region="${region}" >/dev/null 2>&1; then
    audit_pass "analyzer job ${analyzer_job} exists"
    if [[ -n "${scheduler_sa}" ]]; then
      local job_policy
      if job_policy="$(gcloud run jobs get-iam-policy "${analyzer_job}" \
          --project="${project_id}" --region="${region}" --format=json 2>/dev/null)"; then
        if printf '%s' "${job_policy}" | grep -q "\"serviceAccount:${scheduler_sa}\"" \
            && printf '%s' "${job_policy}" | grep -q 'roles/run.invoker'; then
          audit_pass "scheduler SA has run.invoker on ${analyzer_job}"
        else
          audit_warn "scheduler SA missing run.invoker on ${analyzer_job} (grant-scheduler-invoker will add it)"
        fi
      else
        audit_unknown "cannot read IAM policy on job ${analyzer_job}"
      fi
    fi
  else
    audit_warn "analyzer job ${analyzer_job} not deployed yet (expected before grant-scheduler-invoker on first run)"
  fi

  if gcloud scheduler jobs describe "lighter-mm-analyzer-schedule" \
      --project="${project_id}" --location="${region}" >/dev/null 2>&1; then
    audit_pass "scheduler job lighter-mm-analyzer-schedule exists"
  else
    audit_warn "scheduler job lighter-mm-analyzer-schedule not created yet (deploy-analyzer-scheduler step)"
  fi
}

audit_check_worker_pool_prereqs() {
  local project_id="$1"
  audit_section "Cloud Run Worker Pool deploy prerequisites"
  local worker_pool="${WORKER_POOL:-lighter-mm-collector}"
  local region="${REGION:-asia-northeast1}"
  if gcloud run worker-pools describe "${worker_pool}" \
      --project="${project_id}" --region="${region}" >/dev/null 2>&1; then
    audit_pass "worker pool ${worker_pool} exists (will be updated)"
  else
    audit_warn "worker pool ${worker_pool} not found (deploy step will create it)"
  fi
}

audit_print_summary() {
  audit_section "Doctor result"
  if [[ "${AUDIT_FAIL_COUNT}" -eq 0 ]]; then
    if [[ "${AUDIT_UNKNOWN_COUNT}" -gt 0 ]]; then
      printf 'READY (with %s UNKNOWN — review warnings)\n' "${AUDIT_UNKNOWN_COUNT}"
    else
      printf 'READY\n'
    fi
    return 0
  fi
  printf 'NOT READY\n'
  printf 'Failures: %s | Warnings: %s | Unknown: %s\n' \
    "${AUDIT_FAIL_COUNT}" "${AUDIT_WARN_COUNT}" "${AUDIT_UNKNOWN_COUNT}"
  return 1
}

audit_print_failure_help() {
  cat >&2 <<'EOF'

Fix APIs (run once as project admin):
  bash scripts/bootstrap_gcp.sh "${PROJECT_ID}"

Fix Cloud Build IAM (least privilege — see docs/DEPLOY_GCP.md section 5):
  PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
  BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
  for ROLE in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/cloudscheduler.admin; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${BUILD_SA}" --role="${ROLE}"
  done

Re-run read-only audit:
  bash scripts/gcp_doctor.sh --project "${PROJECT_ID}"

EOF
}
