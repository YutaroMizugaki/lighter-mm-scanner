import { fmtJst } from "@/lib/format";
import type { AnalysisStatus, CollectorStatus, Overview } from "@/lib/types";
import { publicCorruptFilesNotice } from "@/lib/public";

type Props = {
  overview: Overview;
  collectorStatus: string | null;
  collectorSyncAt: string | null;
  analysisStatus: string;
  lastAnalysisAt: string | null;
  analysisData: AnalysisStatus | null;
  collectorData: CollectorStatus | null;
  marketDataFetchFailed?: boolean;
};

export default function Diagnostics({
  overview,
  collectorStatus,
  collectorSyncAt,
  analysisStatus,
  lastAnalysisAt,
  analysisData,
  collectorData,
  marketDataFetchFailed = false,
}: Props) {
  const ws = overview.ws ?? collectorData?.ws ?? null;
  const corruptSkipped = analysisData?.corrupt_parquet_files ?? 0;
  const channelCount = ws?.subscribed_channels ?? ws?.acked_channels ?? null;
  const subErrors = ws?.subscription_errors ?? 0;
  const tradeParseErrors = ws?.trade_parse_errors ?? 0;
  const syncFailures = collectorData?.consecutive_sync_failures ?? 0;

  return (
    <section className="diagnostics panel">
      <details>
        <summary>システム診断情報</summary>
        <div className="diagnostics-body">
          <dl className="diagnostics-grid">
            <div>
              <dt>データ収集</dt>
              <dd className={`status-${collectorStatus || "UNKNOWN"}`}>
                {statusLabel(collectorStatus || "UNKNOWN")}
              </dd>
            </div>
            <div>
              <dt>分析</dt>
              <dd className={`status-${analysisStatus}`}>{statusLabel(analysisStatus)}</dd>
            </div>
            <div>
              <dt>実行ID</dt>
              <dd className="tabular">{overview.run_id || "—"}</dd>
            </div>
            <div>
              <dt>最終同期</dt>
              <dd>{fmtJst(collectorSyncAt)}</dd>
            </div>
            <div>
              <dt>最終分析</dt>
              <dd>{fmtJst(lastAnalysisAt)}</dd>
            </div>
            <div>
              <dt>Git SHA</dt>
              <dd className="tabular">
                {overview.git_sha || analysisData?.git_sha || "不明"}
              </dd>
            </div>
            <div>
              <dt>収集プログラムのバージョン</dt>
              <dd className="tabular">{overview.collector_version || "—"}</dd>
            </div>
            <div>
              <dt>WebSocket接続数</dt>
              <dd className="tabular">
                {ws != null
                  ? `${ws.connected_shards ?? "?"}/${ws.total_shards ?? "?"}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>購読チャンネル数</dt>
              <dd className="tabular">{channelCount != null ? channelCount : "—"}</dd>
            </div>
            <div>
              <dt>エラー</dt>
              <dd className="tabular">
                購読 {subErrors}件 · 約定データ解析 {tradeParseErrors}件 · 同期失敗{" "}
                {syncFailures}件
                {marketDataFetchFailed ? " · 市場集計データの取得に失敗" : ""}
              </dd>
            </div>
          </dl>
          {corruptSkipped > 0 && (
            <p className="muted diagnostics-note">
              技術情報: {publicCorruptFilesNotice(corruptSkipped)} ファイルのパスと例外の詳細は公開画面に表示していません。
            </p>
          )}
          {analysisData?.status === "ERROR" && (
            <p className="muted diagnostics-note">
              技術情報: 分析処理でエラーが報告されました。例外の詳細は公開画面に表示していません。
            </p>
          )}
        </div>
      </details>
    </section>
  );
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    UNKNOWN: "不明",
    OK: "正常",
    COMPLETED: "完了",
    COLLECTING: "収集中",
    RUNNING: "実行中",
    DEGRADED: "一部機能に問題",
    STALE: "更新遅延",
    OFFLINE: "停止中",
    ERROR: "エラー",
    NOT_STARTED: "未開始",
    NO_ACTIVE_RUN: "実行なし",
  };
  return labels[status] ?? status;
}
