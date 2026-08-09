# Lighter MM Opportunity Scanner

**READ-ONLY RESEARCH TOOL.**  
This is **not** an automated trading bot. It does **not** place orders, hold API private keys, seed phrases, or wallet signers. It never calls `sendTx` / `sendTxBatch`.

The goal is to watch **all active Lighter perpetual markets** for 24–72 hours and rank which markets look most plausible for a **small personal market maker** to capture bid–ask edge — after measuring spread, two-sided depth, trade activity, spread persistence, short-horizon volatility, and maker markout / adverse selection together.

> Displayed spread × trade count ≠ profit.  
> Queue position, cancel latency, adverse selection, actual fill probability, inventory risk, funding, and slippage must be validated separately (e.g. paper trading) before any live MM.

## What this answers (after 24–72h)

1. Are there any markets worth attempting small-size MM on Lighter?
2. Top 10 markets by **MM Opportunity Score** (not raw spread)
3. Side-by-side: spread, persistence, depth, trades/min, volume, 5s/30s markout, volatility
4. Which names fit **$100 / $500 / $1,000** notionals based on historical displayed depth
5. “Wide spread but avoid” list
6. Whether proceeding to **paper trading** is justified

## Setup

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
uv sync --all-extras
cp .env.example .env
```

## Commands

```bash
# Collect continuously (Ctrl+C to stop safely)
uv run lighter-mm collect

# Collect for a fixed window
uv run lighter-mm collect --hours 24
uv run lighter-mm collect --hours 72

# Runtime / DQ / durable status
uv run lighter-mm status
uv run lighter-mm run-status
uv run lighter-mm cloud-status

# Dashboard JSON + storage estimate
uv run lighter-mm generate-dashboard-data --hours 72
uv run lighter-mm estimate-storage --hours 0.1

# Analyze / rank / report / export
uv run lighter-mm analyze --hours 24
uv run lighter-mm rank --hours 72
uv run lighter-mm report --hours 72
uv run lighter-mm export --hours 72 --format csv
```

Env vars: see `.env.example` (`ENVIRONMENT`, `GCS_BUCKET`, `RUN_TARGET_HOURS`, `LIGHTER_WS_URL`, …).

## Cloud operations (GitHub → GCP → Vercel)

**Do not run the 72h collector on a laptop or GitHub Actions.**

| Piece | Role |
|-------|------|
| GitHub `main` | Source of truth |
| Cloud Build | ruff → pytest → Docker → Artifact Registry → **Cloud Run Worker Pool** |
| GCS | Durable Parquet + `state.json` + public dashboard JSON |
| Vercel (`dashboard/`) | Read-only UI over aggregate JSON only |

- Deploy guide: **[docs/DEPLOY_GCP.md](./docs/DEPLOY_GCP.md)**
- Cloud architecture: **[docs/ARCHITECTURE_CLOUD.md](./docs/ARCHITECTURE_CLOUD.md)**

```text
main push → Cloud Build tests → Worker Pool deploy (1 vCPU / 1Gi / instances=1)
         → resume active run via GCS state + leader lock
         → Vercel rebuilds dashboard (independent of collector)
```

### Cost safety

1. Create a **Billing Budget / Alert** before leaving a worker running.
2. Keep `_INSTANCES=1`. Start at **1 vCPU / 1Gi**.
3. Local smoke disk growth ≈ **105 MB/h** → ~2.5 GB/day, ~7.5 GB/72h (order of magnitude).

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) and [docs/ARCHITECTURE_CLOUD.md](./docs/ARCHITECTURE_CLOUD.md).

Phases:

1. **Data collection** (this repo)
2. Historical analysis
3. Paper market maker *(not implemented)*
4. Small live MM *(not implemented)*

Collector runs without the dashboard. Dashboard shows the last published aggregates even if the collector is stopped.

## Data layout

Local (`ENVIRONMENT=local`):

```
data/
  metadata.db
  book_samples/date=YYYY-MM-DD/hour=HH/*.parquet
  trades/...
  markouts/...
  remote/                 # durable mirror (LocalStorageBackend)
reports/
```

Cloud (`ENVIRONMENT=cloud`):

```
/tmp/lighter-mm/          # hot path only (not durable)
gs://<BUCKET>/lighter-mm/
  runs/<run_id>/books|trades|markouts|state|reports/
  state/active_run.json
  state/leader.lock.json
  public/latest.json      # dashboard (never raw books/trades)
```

Hot order books stay in memory. Only 5-second derived book metrics (plus trades / resolved markouts) are persisted — not every 50ms raw book update. Durable sync rotates/uploads at least every **15 minutes**.

## Metric definitions

| Metric | Meaning |
|--------|---------|
| `spread_bps` | `(ask-bid)/mid * 1e4` from local BBO |
| Depth `±X bp` | Quote notional within X bp of mid on each side |
| `two_sided_depth_*` | `min(bid_depth, ask_depth)` — avoids one-sided illusions |
| Trades/min | **Market-level** print frequency (≠ your maker fill probability) |
| Spread persistence | Fraction of time / run lengths where spread ≥ threshold |
| Volatility | Abs log mid moves over 5s/30s/60s (1s uses consecutive-sample proxy when sampling at 5s) |
| Maker markout | Post-trade mid move from the **maker** perspective (positive = maker-favorable) |

### Maker markout sign

- `is_maker_ask == true` (maker sold): `(trade_price - future_mid) / ref_mid * 1e4`
- `is_maker_ask == false` (maker bought): `(future_mid - trade_price) / ref_mid * 1e4`

Horizons: 1s / 5s / 30s / 60s.

## MM Opportunity Score

Implemented only in `src/lighter_mm/scoring.py` (easy to retune).

Default weight mix (cross-sectional percentile ranks):

- 25 trade activity
- 20 spread (capped diminishing returns)
- 20 two-sided depth
- 25 maker markout
- 10 data quality / persistence

Hard penalties for low coverage, sparse trades, thin books, negative / deeply negative markout.

Candidates are split into letter ranks **A/B/C/D**.

## Lighter API (verified against official docs, 2026-08)

Primary sources:

- https://apidocs.lighter.xyz/
- https://apidocs.lighter.xyz/docs/websocket-reference
- https://apidocs.lighter.xyz/docs/rate-limits
- https://apidocs.lighter.xyz/reference/orderbooks
- https://github.com/elliottech/lighter-python

| Item | Value used |
|------|------------|
| REST | `https://mainnet.zklighter.elliot.ai/api/v1/` |
| Market list | `GET /orderBooks?filter=perp` |
| WS | `wss://mainnet.zklighter.elliot.ai/stream?readonly=true` |
| Streams | `order_book/{id}`, `trade/{id}`, `market_stats/all` |

**Rate limits (official):**

- WS: 255 connections/IP, 500 subscriptions/connection, 200 client msgs/min/IP, 50 inflight, keepalive ≤2 min
- REST Standard: 60 requests / rolling minute

**This tool’s safety defaults:** 450 subs/connection, 150 client msgs/min, exponential backoff + jitter on disconnect/429.

Order book continuity: `begin_nonce` must equal previous `nonce`; on gap the market book is discarded and resubscribed. `size == 0` deletes a level.

## Failure / deploy behavior

- WS drops → reconnect with backoff, rebuild books from snapshot
- Nonce gaps → per-market resync
- SIGINT/SIGTERM / Worker revision → final flush → GCS upload → state update
- main redeploy → resume same `run_id` when `active_run.status=running`
- Leader lock prevents dual collectors (`instances=1` + lease)
- Coverage gaps during deploys are recorded (`deployment_gaps`) and surface as STALE/OFFLINE in the dashboard

## After 72 hours

Cloud: worker finalizes when `RUN_TARGET_HOURS=72`, writes full dashboard JSON, sets `status=completed`.

Local:

```bash
uv run lighter-mm report --hours 72
uv run lighter-mm rank --hours 72
uv run lighter-mm export --hours 72 --format csv
uv run lighter-mm generate-dashboard-data --hours 72
```

## Security

- No API keys, private keys, seed phrases, or wallet connections
- No order placement
- Public/read-only market data only
- No GCP JSON keys in the repo — use ADC / attached Service Account
- Dashboard JSON is public-aggregate only; raw Parquet stays private

## Dev

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
cd dashboard && npm ci && npm run build
```

## Smoke test

```bash
# Local / Lighter mainnet (~5–6 min)
LIGHTER_MM_NO_DASHBOARD=1 uv run lighter-mm collect --hours 0.1
uv run lighter-mm status
uv run lighter-mm run-status
uv run lighter-mm generate-dashboard-data --hours 1
uv run lighter-mm estimate-storage --hours 0.1

# Cloud: see docs/DEPLOY_GCP.md (Worker start, GCS upload, forced redeploy resume)
```
