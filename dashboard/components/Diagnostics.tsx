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
  marketsFetchFailed?: boolean;
};

export default function Diagnostics({
  overview,
  collectorStatus,
  collectorSyncAt,
  analysisStatus,
  lastAnalysisAt,
  analysisData,
  collectorData,
  marketsFetchFailed = false,
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
        <summary>System diagnostics</summary>
        <div className="diagnostics-body">
          <dl className="diagnostics-grid">
            <div>
              <dt>Collector</dt>
              <dd className={`status-${collectorStatus || "UNKNOWN"}`}>
                {collectorStatus || "UNKNOWN"}
              </dd>
            </div>
            <div>
              <dt>Analysis</dt>
              <dd className={`status-${analysisStatus}`}>{analysisStatus}</dd>
            </div>
            <div>
              <dt>Run ID</dt>
              <dd className="tabular">{overview.run_id || "—"}</dd>
            </div>
            <div>
              <dt>Last Sync</dt>
              <dd>{fmtJst(collectorSyncAt)}</dd>
            </div>
            <div>
              <dt>Last Analysis</dt>
              <dd>{fmtJst(lastAnalysisAt)}</dd>
            </div>
            <div>
              <dt>Git SHA</dt>
              <dd className="tabular">
                {overview.git_sha || analysisData?.git_sha || "unknown"}
              </dd>
            </div>
            <div>
              <dt>Collector Version</dt>
              <dd className="tabular">{overview.collector_version || "—"}</dd>
            </div>
            <div>
              <dt>WebSocket Shards</dt>
              <dd className="tabular">
                {ws != null
                  ? `${ws.connected_shards ?? "?"}/${ws.total_shards ?? "?"}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>Channels</dt>
              <dd className="tabular">{channelCount != null ? channelCount : "—"}</dd>
            </div>
            <div>
              <dt>Errors</dt>
              <dd className="tabular">
                sub {subErrors} · trade parse {tradeParseErrors} · sync failures{" "}
                {syncFailures}
                {marketsFetchFailed ? " · markets fetch failed" : ""}
              </dd>
            </div>
          </dl>
          {corruptSkipped > 0 && (
            <p className="muted diagnostics-note">
              Technical details: {publicCorruptFilesNotice(corruptSkipped)} Paths and
              exception text are omitted from the public dashboard.
            </p>
          )}
          {analysisData?.status === "ERROR" && (
            <p className="muted diagnostics-note">
              Technical details: analysis run reported an error. Raw exception text is
              omitted from the public dashboard.
            </p>
          )}
        </div>
      </details>
    </section>
  );
}
