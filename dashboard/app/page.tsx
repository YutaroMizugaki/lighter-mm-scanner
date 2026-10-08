import DataFreshness from "@/components/DataFreshness";
import DataHealthNotice from "@/components/DataHealthNotice";
import Diagnostics from "@/components/Diagnostics";
import Hero from "@/components/Hero";
import MetricCard from "@/components/MetricCard";
import PublicErrorState from "@/components/PublicErrorState";
import ScoreExplainer from "@/components/ScoreExplainer";
import TopOpportunities from "@/components/TopOpportunities";
import {
  getAnalysisStatusResult,
  getCandidatesResult,
  getCollectorStatusResult,
  getOverviewResult,
  resolveDashboardBundle,
} from "@/lib/api";
import { fmt } from "@/lib/format";
import {
  effectiveCollectorStatus,
  effectivePublicAnalysisStatus,
  statusHealthNote,
} from "@/lib/status";
import {
  publicAnalysisPendingMessage,
  publicDataUnavailableMessage,
} from "@/lib/public";
import styles from "./summary.module.css";

export default async function HomePage() {
  const bundle = await resolveDashboardBundle();
  const [
    overviewResult,
    collectorResult,
    analysisStatusResult,
    candidatesResult,
  ] = await Promise.all([
    getOverviewResult(bundle),
    getCollectorStatusResult(),
    getAnalysisStatusResult(),
    getCandidatesResult(bundle),
  ]);
  const configured = Boolean(process.env.NEXT_PUBLIC_DATA_BASE_URL);

  if (!configured) {
    const msg = publicDataUnavailableMessage("config");
    return <PublicErrorState title={msg.title} body={msg.body} />;
  }

  if (!overviewResult.ok) {
    const missing =
      overviewResult.status === 404 ||
      /404|not found|ENOENT/i.test(overviewResult.error);
    const msg = missing
      ? publicAnalysisPendingMessage()
      : publicDataUnavailableMessage("overview");
    return <PublicErrorState title={msg.title} body={msg.body} />;
  }

  const overview = overviewResult.data;
  const collectorData = collectorResult.ok ? collectorResult.data : null;
  const collectorStatus = collectorData
    ? effectiveCollectorStatus(collectorData)
    : null;
  const publicAnalysis = effectivePublicAnalysisStatus(analysisStatusResult, overview);
  const analysisData = publicAnalysis.analysisData;
  const analysisFreshness = {
    status: publicAnalysis.status,
    stale: publicAnalysis.stale,
  };
  const lastAnalysisAt = publicAnalysis.lastAnalysisAt;
  const analysisStatusFetchFailed = publicAnalysis.analysisStatusFetchFailed;
  const candidates = candidatesResult.ok ? candidatesResult.data.candidates ?? [] : [];
  const marketDataFetchFailed = !candidatesResult.ok;

  const analyzed = overview.markets_analyzed ?? overview.markets;
  const analysisError = analysisData?.status === "ERROR";
  const analysisDegraded =
    analysisFreshness.status === "DEGRADED" ||
    analysisData?.status === "DEGRADED" ||
    overview.status === "DEGRADED";
  const corruptSkipped = analysisData?.corrupt_parquet_files ?? 0;
  const collectorNote = collectorStatus
    ? statusHealthNote(collectorStatus, "collector")
    : null;
  const analysisNote = statusHealthNote(analysisFreshness.status, "analysis");
  const collectorSyncAt = collectorData?.last_successful_sync ?? null;

  const healthWarnings = [...(overview.health_warnings || [])];
  if (marketDataFetchFailed) {
    healthWarnings.unshift("市場データを読み込めませんでした。");
  }
  if (analysisStatusFetchFailed) {
    healthWarnings.unshift(
      "分析の更新状況を取得できないため、公開済みの結果を表示しています。",
    );
  }

  const showHealthBanner =
    collectorStatus === "DEGRADED" ||
    collectorStatus === "STALE" ||
    collectorStatus === "OFFLINE" ||
    analysisFreshness.status === "ERROR" ||
    analysisFreshness.status === "STALE" ||
    analysisFreshness.status === "NOT_STARTED" ||
    analysisFreshness.status === "NO_ACTIVE_RUN" ||
    analysisDegraded ||
    healthWarnings.length > 0 ||
    analysisError ||
    Boolean(collectorNote) ||
    Boolean(analysisNote) ||
    marketDataFetchFailed ||
    analysisStatusFetchFailed;

  const primaryMessages = [
    analysisError
      ? "最新の分析に失敗しました。前回の有効な結果を表示している場合があります。"
      : "",
    analysisDegraded && corruptSkipped > 0
      ? "一部のデータを処理できませんでした。直近の有効な分析結果を表示しています。"
      : "",
    ...healthWarnings,
    analysisNote || "",
    collectorNote || "",
  ].filter(Boolean);

  return (
    <>
      <Hero />

      <section
        className={`panel ${styles.panel}`}
        aria-labelledby="summary-heading"
      >
        <div className="section-header">
          <h2 id="summary-heading">市場の概要</h2>
        </div>

        <div className={styles.grid}>
          <MetricCard label="分析した市場" value={analyzed ?? "—"} />
          <MetricCard label="候補の市場" value={overview.candidates ?? 0} />
          <MetricCard
            label="データ取得率"
            value={
              overview.coverage_pct != null
                ? `${overview.coverage_pct.toFixed(1)}%`
                : "—"
            }
          />

        </div>
        <DataFreshness
          status={analysisFreshness.status}
          lastAnalysisAt={lastAnalysisAt}
        />
      </section>

      <TopOpportunities candidates={candidates} fetchFailed={marketDataFetchFailed} />

      <ScoreExplainer />

      <DataHealthNotice
        show={showHealthBanner}
        primaryMessages={primaryMessages}
        corruptSkipped={corruptSkipped}
        lastAnalysisAt={lastAnalysisAt}
        analysisError={analysisError}
        marketDataFetchFailed={marketDataFetchFailed}
      />

      <Diagnostics
        overview={overview}
        collectorStatus={collectorStatus}
        collectorSyncAt={collectorSyncAt}
        analysisStatus={analysisFreshness.status}
        lastAnalysisAt={lastAnalysisAt}
        analysisData={analysisData}
        collectorData={collectorData}
        marketDataFetchFailed={marketDataFetchFailed}
      />

      <section className="disclaimer" aria-labelledby="disclaimer-heading">
        <h2 id="disclaimer-heading">ご利用にあたって</h2>
        <p className="section-lead">
          公開データに基づく調査用ツールです。数値は売買の推奨や利益の保証ではありません。
          実際の取引では、約定確率・逆選択・在庫リスクを別途確認してください。
          仮想取引は注文の順番待ち、通信遅延、キャンセルなどを再現しておらず、実際の注文は行いません。
        </p>
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          観測期間{" "}
          <span className="tabular">
            {overview.run_observation_hours != null
              ? `${fmt(overview.run_observation_hours, 1)}時間`
              : overview.observation_hours != null
                ? `${fmt(overview.observation_hours, 1)}時間`
                : "—"}
          </span>
          {overview.analysis_window_hours != null && (
            <>
              {" "}
              · スコアの集計期間{" "}
              <span className="tabular">
                {fmt(overview.analysis_window_hours, 1)}時間
              </span>
            </>
          )}

        </p>
      </section>
    </>
  );
}
