#!/usr/bin/env bash
# One-time GCP project bootstrap: enable required APIs.
# Run manually with Project Owner / Service Usage Admin — NOT from Cloud Build.
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: set PROJECT_ID or pass as first argument" >&2
  exit 1
fi

echo "Enabling required APIs for project: ${PROJECT_ID}"
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  logging.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="${PROJECT_ID}"

echo "Bootstrap complete. APIs are enabled for ${PROJECT_ID}."
