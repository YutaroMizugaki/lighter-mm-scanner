const { test } = require("node:test");
const assert = require("node:assert/strict");
const ts = require("typescript");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
// Execute the production TypeScript directly without committing build artifacts.
const source = readFileSync(resolve(__dirname, "../lib/dataHealth.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const output = { exports: {} };
new Function("module", "exports", compiled)(output, output.exports);
const { collectionHealth } = output.exports;
const now = Date.parse("2026-10-08T09:00:00Z");
const ago = (minutes) => new Date(now - minutes * 60_000).toISOString();
const fresh = () => ({
  status: "COLLECTING", generated_at: ago(0), last_successful_sync: ago(1),
  last_durable_event_at: ago(1), consecutive_sync_failures: 0,
  ws: { connected_shards: 5, total_shards: 5, planned_channels: 417, acked_channels: 417 },
});
test("healthy collector exposes all three checks", () => {
  const h = collectionHealth(fresh(), now);
  assert.deepEqual(h.checks.map(c => c.level), ["ok", "ok", "ok"]);
  assert.deepEqual(h.issues, []);
});
test("fresh synchronization cannot hide stopped market collection", () => {
  const h = collectionHealth({ ...fresh(), last_durable_event_at: ago(41), last_trade_at: ago(0) }, now);
  assert.equal(h.checks[0].level, "ok");
  assert.equal(h.checks[1].level, "error");
});
test("missing durable evidence cannot be replaced by in-memory samples", () => {
  const h = collectionHealth({ ...fresh(), last_durable_event_at: null, last_usable_book_sample_at: ago(0) }, now);
  assert.equal(h.checks[1].level, "unknown");
});
test("stale report cannot establish current connection or synchronization", () => {
  const h = collectionHealth({ ...fresh(), generated_at: ago(21) }, now);
  assert.equal(h.checks[0].level, "unknown");
  assert.equal(h.checks[2].level, "unknown");
  assert.ok(h.issues.length);
});
test("failed synchronization is diagnosed without exposing internal errors", () => {
  const h = collectionHealth({ ...fresh(), consecutive_sync_failures: 3, last_sync_error: "Bearer secret-token gs://private/path" }, now);
  assert.equal(h.checks[0].level, "error");
  assert.ok(!JSON.stringify(h).includes("secret-token"));
  assert.ok(!JSON.stringify(h).includes("gs://"));
});
test("partial connection and incomplete subscriptions are both detected", () => {
  for (const ws of [{connected_shards: 4, total_shards: 5}, {...fresh().ws, acked_channels: 400}, {...fresh().ws, planned_channels: 0}]) {
    assert.equal(collectionHealth({ ...fresh(), ws }, now).checks[2].level, "warning");
  }
  assert.equal(collectionHealth({ ...fresh(), ws: {connected_shards: 0, total_shards: 5} }, now).checks[2].level, "error");
});
test("book warnings are actionable even while trades keep durable data fresh", () => {
  const h = collectionHealth({ ...fresh(), status: "DEGRADED", health_warnings: ["Usable book samples stale: last sample 100s ago (threshold 30s)."] }, now);
  assert.equal(h.checks[1].level, "warning");
  assert.ok(h.issues.some(s => s.includes("板データ")));
});
test("unexplained DEGRADED and ERROR statuses are never presented as recovered", () => {
  for (const status of ["DEGRADED", "ERROR", "COMPLETED"]) {
    assert.ok(collectionHealth({ ...fresh(), status }, now).issues.length);
  }
});
test("fetch failures, missing connection counters and invalid/future clocks remain unknown", () => {
  assert.ok(collectionHealth(null, now).checks.every(c => c.level === "unknown"));
  for (const stamp of ["invalid", ago(-2)]) {
    const h = collectionHealth({ ...fresh(), generated_at: stamp, last_successful_sync: stamp, last_durable_event_at: stamp }, now);
    assert.ok(h.checks.every(c => c.level === "unknown"));
  }
  assert.equal(collectionHealth({ ...fresh(), ws: null }, now).checks[2].level, "unknown");
});
test("freshness thresholds retain their boundary behavior", () => {
  for (const [minutes, level] of [[20, "ok"], [20.01, "warning"], [40, "warning"], [40.01, "error"]]) {
    assert.equal(collectionHealth({ ...fresh(), last_durable_event_at: ago(minutes) }, now).checks[1].level, level);
  }
});
