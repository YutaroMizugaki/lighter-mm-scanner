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
  getCollectorStatusResult,
  getMarketsResult,
  getOverviewResult,
  resolveDashboardBundle,
} from "@/lib/api";
import { fmt, fmtJst } from "@/lib/format";
import {
  analysisDisplayTimestamp,
  effectiveAnalysisStatus,
  effectiveCollectorStatus,
  statusHealthNote,
} from "@/lib/status";
import {
  formatRelativeAge,
  publicAnalysisPendingMessage,
  publicDataUnavailableMessage,
} from "@/lib/public";

export default async function HomePage() {
  const bundle = await resolveDashboardBundle();
  const overviewResult = await getOverviewResult();
  const collectorResult = await getCollectorStatusResult();
  const analysisStatusResult = await getAnalysisStatusResult();
  const marketsResult = await getMarketsResult(bundle);
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
  const analysisData = analysisStatusResult.ok ? analysisStatusResult.data : null;
  const analysisFreshness = effectiveAnalysisStatus(analysisData);
  const markets = marketsResult.ok ? marketsResult.data.markets ?? [] : [];
  const marketsFetchFailed = !marketsResult.ok;

  const analyzed = overview.markets_analyzed ?? overview.markets;
  const lastAnalysisAt = analysisDisplayTimestamp(analysisData);
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
  if (marketsFetchFailed) {
    healthWarnings.unshift("Market aggregate data could not be loaded.");
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
    marketsFetchFailed;

  const primaryMessages = [
    analysisError
      ? "The latest analysis run failed. Prior valid results may still be shown."
      : "",
    analysisDegraded && corruptSkipped > 0
      ? "Some source data could not be processed. The latest valid analysis is still shown."
      : "",
    ...healthWarnings,
    analysisNote || "",
    collectorNote || "",
  ].filter(Boolean);

  const relativeUpdated = formatRelativeAge(lastAnalysisAt);

  return (
    <>
      <Hero />

      <section className="panel" aria-labelledby="summary-heading">
        <div className="section-header">
          <h2 id="summary-heading">Market summary</h2>
        </div>
        <div className="summary-grid">
          <MetricCard label="Markets analyzed" value={analyzed ?? "—"} />
          <MetricCard label="Candidates" value={overview.candidates ?? 0} />
          <MetricCard
            label="Data coverage"
            value={
              overview.coverage_pct != null
                ? `${overview.coverage_pct.toFixed(1)}%`
                : "—"
            }
          />
          <MetricCard
            label="Last Analysis"
            value={relativeUpdated}
            subtext={fmtJst(lastAnalysisAt)}
            title={fmtJst(lastAnalysisAt)}
          />
        </div>
        <DataFreshness
          status={analysisFreshness.status}
          lastAnalysisAt={lastAnalysisAt}
        />
      </section>

      <TopOpportunities markets={markets} fetchFailed={marketsFetchFailed} />

      <ScoreExplainer />

      <DataHealthNotice
        show={showHealthBanner}
        primaryMessages={primaryMessages}
        corruptSkipped={corruptSkipped}
        lastAnalysisAt={lastAnalysisAt}
        analysisError={analysisError}
        marketsFetchFailed={marketsFetchFailed}
      />

      <Diagnostics
        overview={overview}
        collectorStatus={collectorStatus}
        collectorSyncAt={collectorSyncAt}
        analysisStatus={analysisFreshness.status}
        lastAnalysisAt={lastAnalysisAt}
        analysisData={analysisData}
        collectorData={collectorData}
        marketsFetchFailed={marketsFetchFailed}
      />

      <section className="panel" aria-labelledby="disclaimer-heading">
        <h2 id="disclaimer-heading">Disclaimer</h2>
        <p className="section-lead">
          {overview.disclaimer ||
            "Independent, read-only market research. Estimates are not financial advice and do not guarantee fills, edge, or profit."}
        </p>
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          Observation window{" "}
          <span className="tabular">
            {overview.run_observation_hours != null
              ? `${fmt(overview.run_observation_hours, 1)}h`
              : overview.observation_hours != null
                ? `${fmt(overview.observation_hours, 1)}h`
                : "—"}
          </span>
          {overview.analysis_window_hours != null && (
            <>
              {" "}
              · ranking window{" "}
              <span className="tabular">
                {fmt(overview.analysis_window_hours, 1)}h
              </span>
            </>
          )}
          .
        </p>
      </section>
    </>
  );
}
