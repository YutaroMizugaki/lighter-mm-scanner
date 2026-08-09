# Cloud layer validation (this agent environment)

## Available here

- Unit tests: **19 passed** (includes storage backend, leader lock, estimate)
- Dashboard `npm run build`: success (Next.js 15)
- Local durable mode (~3 min, `ENVIRONMENT=local`):
  - 205 markets, parquet rotate/upload to `data/remote` mirror
  - `state.json` + `active_run.json` + `leader.lock.json` + `public/latest.json`
  - EXIT 0, `status=completed` when `RUN_TARGET_HOURS` reached
- Forced restart resume:
  - same `run_id` resumed (`resumed run …`)
  - fresh WS snapshots after restart

## Not available in this VM

- `gcloud` / Docker daemon / GCP credentials / Vercel tokens
- Therefore real Cloud Build → Artifact Registry → Worker Pool deploy
  and Vercel production deploy must be completed in your GCP/Vercel projects
  using `docs/DEPLOY_GCP.md` after connecting the private GitHub repo.

## Spec used

- Cloud Run Worker Pools: https://docs.cloud.google.com/run/docs/deploy-worker-pools
- `gcloud run worker-pools deploy` with `--instances=1 --cpu=1 --memory=1Gi`
