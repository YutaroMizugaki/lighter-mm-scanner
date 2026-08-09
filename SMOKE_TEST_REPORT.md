# Mainnet smoke test report (2026-08-09)

READ-ONLY collector against Lighter mainnet public API. No keys / no `sendTx`.

## Configuration

- Markets: all active perps via `GET /api/v1/orderBooks?filter=perp`
- WS: `wss://mainnet.zklighter.elliot.ai/stream?readonly=true`
- Streams: `order_book/{id}`, `trade/{id}`, `market_stats/all`
- `MAX_CLIENT_MESSAGES_PER_MINUTE=120`
- `BOOK_SAMPLE_INTERVAL_SECONDS=5`
- Duration: `--hours 0.1` (~6 minutes)

## Results

| Metric | Value |
|--------|------:|
| Active perp markets | 205 |
| Subscriptions sent | 411 (`205*2 + market_stats/all`) |
| WS connections used | 1 shard |
| Dropped connections | **0** |
| Book resyncs | **0** |
| Nonce gaps | **0** |
| Markets ready (peak) | **205/205** |
| Markets ready (end) | ~189/205 (quiet books past 180s stale threshold) |
| Book samples written | 14,555 |
| Trades written | 18,520 |
| Markouts written | 25,189 |
| Avg samples / market | 71 |
| Sample coverage (vs 6min@5s≈72) | ~99% once fully subscribed |
| Process RSS | ~162 MB |
| CPU (steady) | ~6% of one core |
| Disk used (6 min) | ~10.5 MB |
| Disk growth / hour (extrapolated) | ~105 MB/h |
| 72h disk estimate | ~7–8 GB (order of magnitude; depends on trade intensity) |

## Timeline

- t≈0–150s: rate-limited subscribe ramp; ready markets climb 0 → 205
- t≈150–360s: full book+trade+markout collection, 0 disconnects
- Clean SIG/timeout shutdown with Parquet flush (`SMOKE_EXIT:0`)

## Notes / fixes applied during smoke

1. **Subscribe/read interleaving** — must subscribe concurrently with the reader; otherwise huge `order_book` snapshots overflow the receive buffer and the server drops the connection.
2. **Stale threshold** loosened to 180s (quiet books do not emit 50ms diffs when unchanged).
3. **Initial `subscribed/trade` snapshots** are now dedupe-only (not persisted) to avoid thousands of historical date partitions from long-tail trade history.

## Conclusion

Collector is stable enough for 24–72h continuous runs on current mainnet market count (~205 perps) with a single WS connection under the 450-sub safety cap.
