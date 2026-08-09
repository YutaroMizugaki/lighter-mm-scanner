# Architecture — Lighter MM Opportunity Scanner

**READ-ONLY RESEARCH TOOL.** No trading, no keys, no `sendTx` / `sendTxBatch`.

## Purpose

Collect 24–72h of mainnet public market data and rank perpetual markets by
whether a **small retail market maker** could plausibly capture bid–ask edge
after accounting for spread, two-sided depth, trade activity, spread persistence,
short-horizon volatility, and maker markout / adverse selection.

## Data sources (official, verified 2026-08)

| Source | Endpoint / channel |
|--------|-------------------|
| Market discovery | `GET https://mainnet.zklighter.elliot.ai/api/v1/orderBooks?filter=perp` |
| Order book | WS `order_book/{market_id}` |
| Trades | WS `trade/{market_id}` |
| Market stats | WS `market_stats/all` |
| WS URL | `wss://mainnet.zklighter.elliot.ai/stream` (optional `?readonly=true`) |

Docs: https://apidocs.lighter.xyz/  
SDK reference behavior: https://github.com/elliottech/lighter-python (`WsClient`)

### Order book continuity

- Subscription returns a full snapshot (`subscribed/order_book`).
- Subsequent messages are state diffs (`update/order_book`).
- Continuity: `begin_nonce` of update must equal previous `nonce`.
- On gap: discard local book and resubscribe for that market.
- Level with `size == 0` deletes the price level (SDK + docs).

### Rate limits (official)

**WebSocket (per IP):** 255 connections, 500 subscriptions/connection, 200 client
messages/min, 50 inflight, keepalive within 2 minutes.

**REST Standard:** 60 requests / rolling minute.

Defaults used by this tool (safety margin):

- `MAX_SUBSCRIPTIONS_PER_CONNECTION = 450`
- `MAX_CLIENT_MESSAGES_PER_MINUTE = 150`

## Phases

```
Phase 1  Data collection   ← this repo
Phase 2  Historical analysis
Phase 3  Paper market maker (not implemented)
Phase 4  Small live MM (not implemented)
```

## Process layout

```
CLI (typer)
  └─ collect / status / analyze / rank / report / export
       │
       ├─ MarketDiscovery (REST, hourly refresh)
       ├─ WsManager (sharded connections, token-bucket subscribe)
       │     ├─ LocalOrderBook per market
       │     ├─ TradeIngest (dedupe trade_id)
       │     └─ MarketStatsCache
       ├─ BookSampler (every BOOK_SAMPLE_INTERVAL_SECONDS)
       ├─ MarkoutEngine (1s/5s/30s/60s pending queue)
       ├─ MinuteAggregator (in-memory trade buckets)
       ├─ ParquetWriter (rotated hourly partitions)
       ├─ SqliteMeta (runs, markets, DQ counters)
       └─ RichDashboard
```

Analysis path:

```
Parquet + SQLite
  → DuckDB / Polars aggregation
  → scoring.py (MM Opportunity Score)
  → rank / HTML / CSV
```

## Storage

```
data/
  metadata.db
  book_samples/date=YYYY-MM-DD/*.parquet
  trades/date=YYYY-MM-DD/*.parquet
  markouts/date=YYYY-MM-DD/*.parquet
  aggregates/...
reports/
  latest.html
```

Hot path keeps full books in memory; only 5s derived samples are persisted.
Trades and resolved markouts are buffered and flushed frequently for crash safety.

## Scoring (single module)

All ranking logic lives in `src/lighter_mm/scoring.py`:

- Cross-sectional percentile ranks for activity, spread, two-sided depth, markout, persistence/DQ
- Hard penalties for low coverage, sparse trades, thin books, negative markout

## Extensibility

- Market type enum already distinguishes `perp` / `spot` (spot discovery stubbed).
- Collector interfaces accept new streams without changing storage layout.
- Paper MM can consume the same Parquet schemas later.
