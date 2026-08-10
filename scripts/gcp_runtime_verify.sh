#!/usr/bin/env bash
# Post-deploy runtime E2E verification (read-only by default).
#
# Usage:
#   bash scripts/gcp_runtime_verify.sh --project "$PROJECT_ID" --from-trigger lighter-mm-main
#
# Deploy prerequisites (read-only):
#   bash scripts/gcp_doctor.sh --project "$PROJECT_ID" --from-trigger lighter-mm-main
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m lighter_mm.runtime_verify.cli "$@"
