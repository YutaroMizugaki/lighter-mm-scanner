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
```

## Commands

```bash
# Collect continuously (Ctrl+C to stop safely)
uv run lighter-mm collect

# Collect for a fixed window
uv run lighter-mm collect --hours 24
uv run lighter-mm collect --hours 72

# Runtime / DQ status
uv run lighter-mm status

# Analyze / rank / report / export
uv run lighter-mm analyze --hours 24
uv run lighter-mm rank --hours 72
uv run lighter-mm report --hours 72
uv run lighter-mm export --hours 72 --format csv
```

Environment overrides use prefix `LIGHTER_MM_` (see `src/lighter_mm/config.py`), e.g.:

```bash
export LIGHTER_MM_BOOK_SAMPLE_INTERVAL_SECONDS=5
export LIGHTER_MM_WS_URL='wss://mainnet.zklighter.elliot.ai/stream?readonly=true'
```

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md).

Phases:

1. **Data collection** (this repo)
2. Historical analysis
3. Paper market maker *(not implemented)*
4. Small live MM *(not implemented)*

## Data layout

```
data/
  metadata.db                 # SQLite: markets, runs, DQ counters
  book_samples/date=YYYY-MM-DD/*.parquet
  trades/date=YYYY-MM-DD/*.parquet
  markouts/date=YYYY-MM-DD/*.parquet
  aggregates/
reports/
  latest.html
  ranking.csv
```

Hot order books stay in memory. Only 5-second derived book metrics (plus trades / resolved markouts) are persisted — not every 50ms raw book update.

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

## Failure behavior

- WS drops are expected → reconnect with backoff, rebuild books from snapshot
- Nonce gaps → per-market resync
- SIGINT/SIGTERM → flush Parquet buffers, close writers, update SQLite run status
- Restart → resume run id when previous status is `running`; trade_id dedupe cache is process-local (Parquet may contain duplicates across process lifetimes only if IDs wrap outside cache — analysis can dedupe by `trade_id`)

## After 72 hours

```bash
uv run lighter-mm report --hours 72
uv run lighter-mm rank --hours 72
uv run lighter-mm export --hours 72 --format csv
```

Open `reports/latest.html` for the executive answers at the top.

## Security

- No API keys, private keys, seed phrases, or wallet connections
- No order placement
- Public/read-only market data only

## Dev

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

## Smoke test

```bash
uv run lighter-mm collect --hours 0.085   # ~5 minutes
uv run lighter-mm status
uv run lighter-mm analyze --hours 1
```
