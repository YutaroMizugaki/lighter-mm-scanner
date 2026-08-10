# Deploy Lighter MM Scanner on Google Cloud + Vercel

**READ-ONLY research collector.** No Lighter keys, wallets, or order APIs.

Official references used for this guide (verify current pages if UI labels change):

- Cloud Run Worker Pools deploy: https://docs.cloud.google.com/run/docs/deploy-worker-pools
- `gcloud run worker-pools deploy`: https://docs.cloud.google.com/sdk/gcloud/reference/run/worker-pools/deploy
- Manual scaling (`--instances`): https://docs.cloud.google.com/run/docs/configuring/workerpools/manual-scaling
- Cloud Build: https://cloud.google.com/build/docs
- Artifact Registry: https://cloud.google.com/artifact-registry/docs
- Cloud Storage: https://cloud.google.com/storage/docs
- Vercel Git integration: https://vercel.com/docs/git

## Architecture

```
GitHub (private) main
  ├─ Cloud Build → tests → Docker → Artifact Registry
  │                 → Cloud Run Worker Pool (Collector, 1 instance)
  │                 → Cloud Run Job (Analyzer, */15 via Cloud Scheduler)
  │                 → Private GCS (immutable Parquet + state)
  │                 → Public GCS (dashboard JSON)
  └─ Vercel → Next.js dashboard reads public aggregate JSON only
```

Collector and Analyzer are **decoupled**. Collector publishes `collector_status.json`; Analyzer publishes `latest.json`, `analysis_status.json`, and related aggregates.

## 1) Create GCP project + billing

```bash
gcloud projects create lighter-mm-scanner --name="Lighter MM Scanner"
gcloud config set project lighter-mm-scanner
# Enable billing in Console: https://console.cloud.google.com/billing
```

**Set a budget alert** (Billing → Budgets & alerts). Suggested starter: $20–50/month with email at 50/90/100%.

## 2) Enable APIs (one-time admin bootstrap)

**Run once** with Project Owner or Service Usage Admin (`serviceusage.services.enable`).
Normal `cloudbuild.yaml` deploys do **not** enable APIs.

```bash
./scripts/bootstrap_gcp.sh
# or manually:
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  logging.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

If APIs are already enabled, this is a no-op. Cloud Build preflight checks API availability
and fails with a clear message if bootstrap was skipped.

Before triggering Cloud Build, run the read-only doctor (uses the same checks as preflight):

```bash
bash scripts/gcp_doctor.sh --project "$PROJECT_ID" --from-trigger lighter-mm-main
# or pass substitutions explicitly — see scripts/gcp_doctor.sh --help
```

Exit `0` + `SAFE TO RETRY CLOUD BUILD` means known blockers are cleared.

After deploy, verify the live pipeline (read-only by default):

```bash
bash scripts/gcp_runtime_verify.sh \
  --project "$PROJECT_ID" \
  --from-trigger lighter-mm-main \
  --gcs-bucket "$GCS_BUCKET" \
  --gcs-public-bucket "$GCS_PUBLIC_BUCKET" \
  --dashboard-url "https://your-dashboard.vercel.app"
```

Optional immediate analyzer run (action mode — executes the Cloud Run Job):

```bash
bash scripts/gcp_runtime_verify.sh \
  --project "$PROJECT_ID" \
  --from-trigger lighter-mm-main \
  --gcs-bucket "$GCS_BUCKET" \
  --gcs-public-bucket "$GCS_PUBLIC_BUCKET" \
  --execute-analyzer
```

`gcp_doctor.sh` = deploy **prerequisites** (IAM, buckets, APIs).  
`gcp_runtime_verify.sh` = deploy **after** runtime E2E health.

## 3) Artifact Registry

```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION=asia-northeast1

gcloud artifacts repositories create lighter-mm \
  --repository-format=docker \
  --location="$REGION" \
  --description="Lighter MM collector images"
```

## 4) GCS buckets

Use **two buckets** (required for Vercel):

1. Private raw data (`GCS_BUCKET`) — keep **Public access prevention** on; never `allUsers`
2. Public aggregates only (`GCS_PUBLIC_BUCKET`) — dashboard JSON; `allUsers` objectViewer OK

> GCP does **not** allow IAM conditions on `allUsers`. Do not try to publish only a
> prefix of the private bucket; use a separate public bucket instead.

```bash
export PRIVATE_BUCKET="${PROJECT_ID}-lighter-mm"
export PUBLIC_BUCKET="${PROJECT_ID}-lighter-mm-public"

gcloud storage buckets create "gs://${PRIVATE_BUCKET}" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets create "gs://${PUBLIC_BUCKET}" --location="$REGION" --uniform-bucket-level-access

# Public read for aggregate JSON bucket only (no conditions — whole bucket is public JSON)
gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" \
  --member=allUsers \
  --role=roles/storage.objectViewer
```

Collector writes raw Parquet to the private bucket and mirrors `lighter-mm/public/*.json`
to `GCS_PUBLIC_BUCKET` when that env is set.

**Do not make raw `trades/` / `books/` publicly readable.**

## 5) Service accounts (least privilege)

```bash
# Runtime collector + analyzer SA (shared by default)
gcloud iam service-accounts create lighter-mm-collector \
  --display-name="Lighter MM Collector"

export COLLECTOR_SA="lighter-mm-collector@${PROJECT_ID}.iam.gserviceaccount.com"

# Optional dedicated Scheduler invocation SA (recommended; falls back to COLLECTOR_SA)
gcloud iam service-accounts create lighter-mm-scheduler \
  --display-name="Lighter MM Scheduler"

export SCHEDULER_SA="lighter-mm-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
```

Object admin on private + public buckets (raw + dashboard JSON):

```bash
gcloud storage buckets add-iam-policy-binding "gs://${PRIVATE_BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" \
  --role=roles/storage.objectAdmin

gcloud storage buckets add-iam-policy-binding "gs://${PUBLIC_BUCKET}" \
  --member="serviceAccount:${COLLECTOR_SA}" \
  --role=roles/storage.objectAdmin
```

Cloud Build deploy SA permissions (project-level, build only — not collector runtime):

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role=roles/run.admin

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role=roles/iam.serviceAccountUser

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role=roles/artifactregistry.writer

# Cloud Scheduler job create/update (deploy step)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" \
  --role=roles/cloudscheduler.admin
```

`roles/run.invoker` on the Analyzer Job for the Scheduler SA is granted automatically by `cloudbuild.yaml` on each deploy.

Do **not** grant Owner/Editor to the collector runtime SA.

## 6) Connect Cloud Build ↔ GitHub private repo

Console path (recommended for private repos):

1. Cloud Build → Repositories → Connect repository
2. Choose GitHub (Cloud Build GitHub App)
3. Select `lighter-mm-scanner` (private)
4. Create trigger:
   - Event: Push to branch
   - Branch: `^main$`
   - Config: `cloudbuild.yaml`
   - Substitutions:
     - `_REGION=asia-northeast1`
     - `_AR_REPO=lighter-mm`
     - `_WORKER_POOL=lighter-mm-collector`
     - `_GCS_BUCKET=<PRIVATE_BUCKET>`
     - `_GCS_PUBLIC_BUCKET=<PUBLIC_BUCKET>`
     - `_SERVICE_ACCOUNT=<COLLECTOR_SA>`
     - `_SCHEDULER_SERVICE_ACCOUNT=<SCHEDULER_SA>` (optional; defaults to `_SERVICE_ACCOUNT`)
     - `_CPU=1`
     - `_MEMORY=1Gi`
     - `_INSTANCES=1`
     - `_RUN_TARGET_HOURS=72`

PR branches should **not** deploy the worker pool (trigger is main-only).

## 7) Manual first deploy (optional)

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_GCS_BUCKET=${PRIVATE_BUCKET},_GCS_PUBLIC_BUCKET=${PUBLIC_BUCKET},_SERVICE_ACCOUNT=${COLLECTOR_SA}
```

Or deploy an already-built image:

```bash
gcloud run worker-pools deploy lighter-mm-collector \
  --region="$REGION" \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/lighter-mm/collector:latest" \
  --cpu=1 \
  --memory=1Gi \
  --instances=1 \
  --service-account="$COLLECTOR_SA" \
  --set-env-vars="ENVIRONMENT=cloud,GCS_BUCKET=${PRIVATE_BUCKET},GCS_PUBLIC_BUCKET=${PUBLIC_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},RUN_TARGET_HOURS=72,STRUCTURED_LOGGING=true,LIGHTER_MM_NO_DASHBOARD=1"
```

## 8) Verify collector and analyzer

```bash
# Collector logs
gcloud logging read 'resource.type="cloud_run_worker_pool" OR textPayload:"collector_started"' --limit=50

# Manual analyzer execution
gcloud run jobs execute lighter-mm-analyzer \
  --region="$REGION" \
  --wait

gcloud run jobs executions list \
  --job=lighter-mm-analyzer \
  --region="$REGION" \
  --limit=3

# Scheduler manual trigger
gcloud scheduler jobs run lighter-mm-analyzer-schedule \
  --location="$REGION"

gcloud run jobs executions list \
  --job=lighter-mm-analyzer \
  --region="$REGION" \
  --limit=3

# State / public JSON after ~1–2 minutes
gcloud storage cat "gs://${PRIVATE_BUCKET}/lighter-mm/state/active_run.json"
gcloud storage cat "gs://${PUBLIC_BUCKET}/lighter-mm/public/analysis_status.json"
```

Expected events: `collector_started`, `market_discovered`, `ws_connected`, `parquet_flushed`, `gcs_uploaded`.

## 9) Forced restart / resume test

```bash
# Redeploy same image → new revision; instances=1 + leader lock + active_run resume
gcloud run worker-pools deploy lighter-mm-collector \
  --region="$REGION" \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/lighter-mm/collector:latest" \
  --cpu=1 --memory=1Gi --instances=1 \
  --service-account="$COLLECTOR_SA" \
  --set-env-vars="ENVIRONMENT=cloud,GCS_BUCKET=${PRIVATE_BUCKET},GCS_PUBLIC_BUCKET=${PUBLIC_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},RUN_TARGET_HOURS=72,STRUCTURED_LOGGING=true,LIGHTER_MM_NO_DASHBOARD=1"

# Confirm same run_id continues
gcloud storage cat "gs://${PRIVATE_BUCKET}/lighter-mm/state/active_run.json"
```

Order books are always rebuilt from fresh Lighter snapshots after restart (never restored from disk).

## 10) Vercel dashboard

1. Import the GitHub private repo in Vercel
2. Root Directory: `dashboard`
3. Framework: Next.js
4. Env:
   - `NEXT_PUBLIC_DATA_BASE_URL=https://storage.googleapis.com/<PUBLIC_BUCKET>/lighter-mm/public`
5. Production branch: `main` (PR → Preview)

Collector deploy is independent of Vercel build success.

## 11) Start / monitor a 72h run

Cloud env already sets `RUN_TARGET_HOURS=72`. On main deploy the worker resumes an active run or starts a new one.

Local simulation:

```bash
ENVIRONMENT=local RUN_TARGET_HOURS=0.1 LIGHTER_MM_NO_DASHBOARD=1 \
  uv run lighter-mm collect
uv run lighter-mm run-status
uv run lighter-mm generate-dashboard-data --hours 1
uv run lighter-mm estimate-storage --hours 0.1
```

## 12) Cost notes (from local smoke ~6 min)

Measured disk growth ≈ **105 MB/hour** (books+trades+markouts). Extrapolated:

| Window | Storage |
|--------|---------|
| 24h | ~2.5 GB |
| 72h | ~7.5 GB |
| 30d | ~75 GB |

Cloud Run Worker Pool @ 1 vCPU / 1Gi, always-on, is the main compute cost. Keep `_INSTANCES=1`. Raise CPU/RAM only if smoke shows pressure.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No GCS objects | IAM on collector SA; `GCS_BUCKET` env |
| Dual writers | `_INSTANCES` must be `1`; inspect `leader.lock.json` |
| Dashboard empty | public URL + analyzer publishes `latest.json`; CORS not required for simple GET from Next server |
| Coverage gaps | expected during deploys; shown as STALE/OFFLINE + deployment_gaps |
| **Dashboard Last Analysis stops moving** | Scheduler + Analyzer job (see below) |
| **Coverage looks artificially low** | Analyzer caps analysis at `last_successful_flush`; check collector sync |
| **Poison final analysis request** | After 3 failures request status=`failed`; incremental analysis resumes |

### Dashboard health labels

- **Last Sync** — from `collector_status.json` (`last_successful_sync`)
- **Last Analysis** — from `analysis_status.json` (`last_successful_analysis_at`)

### Dashboard Last Analysis stops moving

Check Scheduler configuration:

```bash
gcloud scheduler jobs describe lighter-mm-analyzer-schedule \
  --location=asia-northeast1
```

Confirm the HTTP target URI uses the Cloud Run Jobs **v2** run endpoint:

`https://run.googleapis.com/v2/projects/<PROJECT_ID>/locations/<REGION>/jobs/lighter-mm-analyzer:run`

Trigger manually:

```bash
gcloud scheduler jobs run lighter-mm-analyzer-schedule \
  --location=asia-northeast1
```

Check execution:

```bash
gcloud run jobs executions list \
  --job=lighter-mm-analyzer \
  --region=asia-northeast1 \
  --limit=3
```

Check analyzer logs:

```bash
gcloud logging read \
  'resource.type="cloud_run_job"' \
  --limit=100
```

Check public status:

```bash
gcloud storage cat "gs://${PUBLIC_BUCKET}/lighter-mm/public/analysis_status.json"
```
