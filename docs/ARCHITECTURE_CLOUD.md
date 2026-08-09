# Cloud architecture addendum

Extends [ARCHITECTURE.md](../ARCHITECTURE.md). Analysis logic stays primary; cloud is an operations layer.

## Components

| Component | Role |
|-----------|------|
| GitHub private repo | Source of truth |
| Cloud Build (`cloudbuild.yaml`) | main-only: ruff → pytest → Docker → AR → Worker Pool |
| Cloud Run Worker Pool | Long-lived collector (`--instances=1`, 1 vCPU / 1Gi) |
| GCS | Durable Parquet + `state.json` + public dashboard JSON |
| Vercel (`dashboard/`) | Read-only UI over aggregate JSON |

## Data flow

```
Lighter WS
  → Collector (Worker Pool)
  → /tmp/lighter-mm Parquet (hot)
  → every ≤15m: rotate + upload to gs://…/runs/<run_id>/…
  → state.json + public/latest.json
  → Vercel fetches public JSON
```

## Resume / single writer

1. `lighter-mm/state/active_run.json` points at current run
2. `leader.lock.json` lease prevents dual collectors during rolling deploys
3. On start: acquire lock → load state → resume same `run_id` if `status=running`
4. Order books always rebuilt from fresh WS snapshots (not restored)
5. Trade `trade_id` dedupe is process-local + analysis can dedupe by id within partitions

## Status labels (dashboard)

| Label | Meaning |
|-------|---------|
| COLLECTING | running + flush &lt; 20m |
| STALE | flush 20–40m |
| OFFLINE | running but flush &gt; 40m |
| COMPLETED | target hours finished |
| ERROR | failed / missing state |

## Decoupling

- Collector does not call Vercel
- Dashboard never starts/stops the worker
- Dashboard never reads raw trades/books Parquet
