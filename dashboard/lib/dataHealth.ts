import type { CollectorStatus } from "./types";

export type HealthLevel = "ok" | "warning" | "error" | "unknown";
export type HealthCheck = {
  id: "sync" | "market" | "connection";
  label: string;
  level: HealthLevel;
  detail: string;
  timestamp: string | null;
};
export type CollectionHealth = {
  checks: HealthCheck[];
  issues: string[];
  checkedAt: string;
  reportedAt: string | null;
};

function validTimestamp(value: string | null | undefined, now: number): string | null {
  if (!value) return null;
  const ms = Date.parse(value);
  // Future timestamps beyond clock skew cannot establish freshness.
  return Number.isFinite(ms) && ms <= now + 60_000 ? value : null;
}

/** Return only allowlisted public facts; never forward raw errors or collector payloads. */
export function collectionHealth(
  collector: CollectorStatus | null,
  now = Date.now(),
): CollectionHealth {
  const checkedAt = new Date(now).toISOString();
  const reportedAt = validTimestamp(collector?.generated_at, now);
  const reportFresh = reportedAt != null && now - Date.parse(reportedAt) <= 20 * 60_000;
  const checks: HealthCheck[] = [
    { id: "sync", label: "同期", level: "unknown", detail: "同期状況を確認できません。", timestamp: null },
    { id: "market", label: "市場データ", level: "unknown", detail: "保存済みデータの更新時刻を確認できません。", timestamp: null },
    { id: "connection", label: "接続", level: "unknown", detail: "接続状態を確認できません。", timestamp: null },
  ];
  const issues: string[] = [];
  if (!collector) {
    issues.push("収集状況を取得できませんでした。再確認しても続く場合は管理者による確認が必要です。");
    return { checks, issues, checkedAt, reportedAt };
  }
  const sync = checks[0];
  sync.timestamp = validTimestamp(collector.last_successful_sync, now);
  if ((collector.consecutive_sync_failures ?? 0) > 0 || collector.last_sync_error) {
    sync.level = "error";
    sync.detail = `同期に失敗しています（連続${collector.consecutive_sync_failures ?? 0}回）。`;
    issues.push("保存先への同期に失敗しています。再試行で回復しない場合は、保存先の接続・権限を管理者が確認してください。");
  } else if (sync.timestamp) {
    const age = (now - Date.parse(sync.timestamp)) / 60_000;
    sync.level = age > 40 ? "error" : age > 20 ? "warning" : reportFresh ? "ok" : "unknown";
    sync.detail = age > 20 ? "同期の更新が遅れています。" : reportFresh ? "直近の同期は成功しています。" : "最新の同期状況を確認できません。";
  }
  const market = checks[1];
  // In-memory trades/book samples and sync wall-clock do not prove durable collection.
  market.timestamp = validTimestamp(collector.last_durable_event_at, now);
  if (market.timestamp) {
    const age = (now - Date.parse(market.timestamp)) / 60_000;
    market.level = age > 40 ? "error" : age > 20 ? "warning" : "ok";
    market.detail = age > 40 ? "市場データの保存を40分以上確認できていません。" : age > 20 ? "保存済みの市場データは20分以上前のものです。" : "20分以内の市場データが保存されています。";
  }
  const connection = checks[2];
  const ws = collector.ws;
  if (reportFresh && ws && ws.total_shards != null && ws.total_shards > 0 && ws.connected_shards != null) {
    const planned = ws.planned_channels ?? ws.subscribed_channels;
    const acked = ws.acked_channels ?? ws.subscribed_channels;
    const hasBreakdown = ws.required_channels != null && ws.confirmed_required_channels != null && ws.sent_channels != null;
    const incomplete = hasBreakdown
      ? (planned != null && ws.sent_channels! < planned) || ws.confirmed_required_channels! < ws.required_channels!
      : planned != null && acked != null && acked < planned;
    connection.level = ws.connected_shards === 0 ? "error" : ws.connected_shards < ws.total_shards || incomplete || planned === 0 ? "warning" : planned == null || acked == null ? "unknown" : "ok";
    connection.detail = `${ws.connected_shards}/${ws.total_shards}接続${hasBreakdown
      ? ` · 板・統計 ${ws.confirmed_required_channels}/${ws.required_channels}確認済み${ws.pending_trade_channels ? ` · 約定 ${ws.pending_trade_channels}チャンネルは受信待ち` : ""}`
      : planned != null && acked != null ? ` · ${acked}/${planned}チャンネル受信確認済み` : " · 購読状況は不明"}`;
    connection.timestamp = reportedAt;
  }
  if (connection.level === "error" || connection.level === "warning") {
    issues.push("接続または購読の確認が不足しています。自動再接続で回復しない場合は、配信元と購読設定を管理者が確認してください。");
  }
  const warnings = collector.health_warnings ?? [];
  if (warnings.some((w) => /usable book samples stale|book rows are stale|no usable book samples/i.test(w))) {
    market.level = "warning";
    if (market.timestamp && now - Date.parse(market.timestamp) > 40 * 60_000) market.level = "error";
    market.detail += " 利用可能な板データの更新に問題があります。";
    issues.push("板データの更新を確認できません。接続が正常でも、購読と板の再同期を管理者が確認してください。");
  }
  if (market.level === "warning" || market.level === "error") {
    issues.push("市場データに更新遅延があります。表示中の集計には過去のデータが含まれます。");
  }
  if (!reportFresh) issues.push("収集状況の報告が古い、または時刻が不明です。現在の接続・同期が正常かは確認できません。");
  if (collector.status === "COMPLETED") issues.push("このデータ収集は完了しています。新しい市場データの収集は行われていません。");
  else if (collector.status === "ERROR") issues.push("収集処理でエラーが報告されています。管理者による確認が必要です。");
  else if (collector.status === "DEGRADED" && issues.length === 0) issues.push("収集処理が品質低下を報告しています。同期・保存時刻・接続以外の原因は、管理者が収集ログで確認してください。");
  return { checks, issues: [...new Set(issues)], checkedAt, reportedAt };
}
