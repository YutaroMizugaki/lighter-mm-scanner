import {
  analysisDisplayTimestamp,
  effectiveAnalysisStatus,
  effectiveCollectorStatus,
  fmt,
  fmtJst,
  getAnalysisStatusResult,
  getCollectorStatusResult,
  getMarketsResult,
  getOverviewResult,
  resolveDashboardBundle,
  statusHealthNote,
} from "@/lib/data";
import Link from "next/link";

export default async function HomePage() {
  const bundle = await resolveDashboardBundle();
  const overviewResult = await getOverviewResult();
  const collectorResult = await getCollectorStatusResult();
  const analysisStatusResult = await getAnalysisStatusResult();
  const marketsResult = await getMarketsResult(bundle);
  const configured = Boolean(process.env.NEXT_PUBLIC_DATA_BASE_URL);

  if (!configured) {
    return (
      <section className="card">
        <h2>Dashboard data URL not configured</h2>
        <p className="muted">
          Set <code>NEXT_PUBLIC_DATA_BASE_URL</code> to the public HTTPS prefix for aggregate JSON
          (e.g. <code>https://storage.googleapis.com/&lt;BUCKET&gt;/lighter-mm/public</code>).
        </p>
        <p className="muted">
          Collector and dashboard are decoupled — the collector keeps running even if this UI is
          offline.
        </p>
      </section>
    );
  }

  if (!overviewResult.ok) {
    const missing =
      overviewResult.status === 404 ||
      /404|not found|ENOENT/i.test(overviewResult.error);
    return (
      <section className="card">
        <h2>{missing ? "No latest.json yet" : "Failed to load latest.json"}</h2>
        <p className="muted">
          {missing
            ? "Waiting for the analyzer to publish dashboard aggregates. Check Cloud Logging / "
            : "Could not fetch public dashboard JSON. "}
          {!missing && (
            <>
              Error: <code>{overviewResult.error}</code>. Check{" "}
              <code>NEXT_PUBLIC_DATA_BASE_URL</code>, bucket IAM, and CORS.{" "}
            </>
          )}
          {missing && (
            <>
              <code>lighter-mm run-status</code> or trigger the analyzer job manually.
            </>
          )}
        </p>
      </section>
    );
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

  const target = overview.observation_target_hours;
  const obs = overview.run_observation_hours ?? overview.observation_hours;
  const analysisWindow = overview.analysis_window_hours;
  const top = overview.top_candidate;
  const healthWarnings = [...(overview.health_warnings || [])];
  if (marketsFetchFailed) {
    healthWarnings.unshift("Market aggregate data could not be loaded.");
  }
  const warnings = markets
    .flatMap((m) => (m.warnings || []).map((w) => ({ symbol: m.symbol, w })))
    .slice(0, 12);
  const discovered = overview.markets_discovered;
  const analyzed = overview.markets_analyzed ?? overview.markets;
  const collectorNote = collectorStatus
    ? statusHealthNote(collectorStatus, "collector")
    : null;
  const analysisNote = statusHealthNote(analysisFreshness.status, "analysis");
  const collectorSyncAt = collectorData?.last_successful_sync ?? null;
  const lastDataAt =
    collectorData?.last_durable_event_at ??
    overview.last_data_at ??
    overview.last_update ??
    null;
  const lastAnalysisAt = analysisDisplayTimestamp(analysisData);
  const analysisError = analysisData?.status === "ERROR" ? analysisData.error : null;
  const analysisDegraded =
    analysisFreshness.status === "DEGRADED" ||
    analysisData?.status === "DEGRADED" ||
    overview.status === "DEGRADED";
  const corruptSkipped = analysisData?.corrupt_parquet_files ?? 0;
  const skippedFiles = analysisData?.skipped_files ?? [];
  const ws = overview.ws;

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
    collectorNote ||
    analysisNote ||
    marketsFetchFailed;

  const healthBannerPrimary =
    analysisError ||
    (analysisDegraded && corruptSkipped > 0
      ? `DEGRADED — ${corruptSkipped} corrupted Parquet file(s) skipped.`
      : null) ||
    healthWarnings[0] ||
    analysisNote ||
    collectorNote ||
    "Check collector and analyzer status.";

  return (
    <>
      {showHealthBanner && (
        <section className="note">
          <strong>Data health:</strong> {healthBannerPrimary}
          {analysisDegraded && lastAnalysisAt && (
            <p className="muted" style={{ marginTop: "0.4rem", fontSize: "0.85rem" }}>
              Latest valid analysis: {fmtJst(lastAnalysisAt)}
            </p>
          )}
          {analysisDegraded && skippedFiles.length > 0 && (
            <ul className="compact" style={{ marginTop: "0.4rem" }}>
              {skippedFiles.slice(0, 5).map((entry) => (
                <li key={entry.path}>
                  一部の収集データを読み込めませんでした（破損ファイルを除外して解析を継続しています）。
                  <br />
                  <span className="muted">
                    Skipped corrupt parquet: <code>{entry.path}</code>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {analysisError && lastAnalysisAt && (
            <p className="muted" style={{ marginTop: "0.4rem", fontSize: "0.85rem" }}>
              Last successful analysis: {fmtJst(lastAnalysisAt)}
            </p>
          )}
          {marketsFetchFailed && (
            <p className="muted" style={{ marginTop: "0.4rem", fontSize: "0.85rem" }}>
              Dashboard data fetch failed. {marketsResult.error}
            </p>
          )}
          {healthWarnings.length > 1 && (
            <ul className="compact">
              {healthWarnings.slice(1).map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {collectorNote && !analysisError && (
            <p className="muted" style={{ marginTop: "0.4rem" }}>
              Collector: {collectorNote}
            </p>
          )}
          {analysisNote && !analysisError && (
            <p className="muted" style={{ marginTop: "0.4rem" }}>
              Analysis: {analysisNote}
            </p>
          )}
        </section>
      )}
      <section className="card">
        <div className="grid">
          <div className="kpi">
            <div className="label">Last Data</div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {fmtJst(lastDataAt)}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Collector</div>
            <div className={`value status-${collectorStatus || "UNKNOWN"}`}>
              {collectorStatus || "UNKNOWN"}
            </div>
            <div className="label" style={{ marginTop: "0.5rem" }}>Last Sync</div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {fmtJst(collectorSyncAt)}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Analysis</div>
            <div className={`value status-${analysisFreshness.status}`}>
              {analysisFreshness.status}
            </div>
            <div className="label" style={{ marginTop: "0.5rem" }}>Last Analysis</div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {fmtJst(lastAnalysisAt)}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Run</div>
            <div className="value">{overview.run_id || "—"}</div>
          </div>
          <div className="kpi">
            <div className="label">Observation</div>
            <div className="value">
              {obs != null ? `${obs.toFixed(1)}h` : "—"}
              {target != null && target > 0 ? ` / ${target}h` : ""}
            </div>
            {analysisWindow != null && (
              <div className="muted" style={{ fontSize: "0.8rem", marginTop: "0.35rem" }}>
                Ranking window {analysisWindow.toFixed(1)}h
              </div>
            )}
          </div>
          <div className="kpi">
            <div className="label">Markets</div>
            <div className="value">
              {discovered != null && discovered > 0 ? `${analyzed}/${discovered}` : analyzed}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Coverage</div>
            <div className="value">
              {overview.coverage_pct != null ? `${overview.coverage_pct.toFixed(1)}%` : "—"}
            </div>
          </div>
        </div>
        <p className="muted" style={{ marginTop: "0.8rem" }}>
          git {overview.git_sha || "unknown"} · collector {overview.collector_version || "?"} ·
          Generated: {fmtJst(overview.generated_at)}
          {ws != null && (
            <>
              {" "}
              · WS {ws.connected_shards ?? "?"}/{ws.total_shards ?? "?"} shards ·{" "}
              {ws.subscribed_channels ?? "?"} ch
              {(ws.subscription_errors ?? 0) > 0 && (
                <> · sub_err {ws.subscription_errors}</>
              )}
              {(ws.trade_parse_errors ?? 0) > 0 && (
                <> · trade_parse_err {ws.trade_parse_errors}</>
              )}
            </>
          )}
        </p>
      </section>

      <section className="card">
        <h2>Top Candidate</h2>
        {top ? (
          <p>
            <Link href={`/markets/${encodeURIComponent(top.symbol)}`}>
              <strong>{top.symbol}</strong>
            </Link>{" "}
            · score {fmt(top.score)} · spread {fmt(top.median_spread_bps)}bp · m5{" "}
            {fmt(top.maker_markout_5s_median_bps, 2, true)}bp · m30{" "}
            {fmt(top.maker_markout_30s_median_bps, 2, true)}bp
          </p>
        ) : (
          <p className="muted">No ranked markets yet.</p>
        )}
      </section>

      <section className="card">
        <h2>Top MM Candidates (preview)</h2>
        {marketsFetchFailed ? (
          <p className="muted">Market aggregate data could not be loaded.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Symbol</th>
                  <th>Rank</th>
                  <th>Score</th>
                  <th>Spread</th>
                  <th>%≥5bp</th>
                  <th>Depth10</th>
                  <th>TPM med</th>
                  <th>TPM avg</th>
                  <th>M5</th>
                  <th>M30</th>
                  <th>Funding</th>
                </tr>
              </thead>
              <tbody>
                {markets
                  .filter((m) => m.is_candidate)
                  .slice(0, 15)
                  .map((m, i) => (
                    <tr key={m.symbol}>
                      <td>{i + 1}</td>
                      <td>
                        <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
                      </td>
                      <td>
                        <span className={`badge ${m.letter_rank}`}>{m.letter_rank}</span>
                      </td>
                      <td>{fmt(m.score, 1)}</td>
                      <td>{fmt(m.median_spread_bps)}</td>
                      <td>
                        {m.pct_time_spread_ge_5bps != null
                          ? `${(m.pct_time_spread_ge_5bps * 100).toFixed(0)}%`
                          : "—"}
                      </td>
                      <td>{fmt(m.median_two_sided_depth_10bps_usd, 0)}</td>
                      <td>{fmt(m.trades_per_minute_median)}</td>
                      <td>{fmt(m.trades_per_minute_mean)}</td>
                      <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                      <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
                      <td>{fmt(m.current_funding_rate, 4)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
        {!marketsFetchFailed && !markets.some((m) => m.is_candidate) && (
          <p className="muted">No candidates yet (need longer observation / filters).</p>
        )}
      </section>

      <section className="card">
        <h2>Warnings</h2>
        {warnings.length ? (
          <ul className="compact">
            {warnings.map((x, i) => (
              <li key={`${x.symbol}-${i}`}>
                <Link href={`/markets/${encodeURIComponent(x.symbol)}`}>{x.symbol}</Link>: {x.w}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No warnings in latest aggregates.</p>
        )}
      </section>
    </>
  );
}
