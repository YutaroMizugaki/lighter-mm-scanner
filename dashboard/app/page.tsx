import { fmt, getMarkets, getOverview } from "@/lib/data";
import Link from "next/link";

export default async function HomePage() {
  const overview = await getOverview();
  const markets = await getMarkets();
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

  if (!overview) {
    return (
      <section className="card">
        <h2>No latest.json yet</h2>
        <p className="muted">
          Waiting for the collector to publish dashboard aggregates. Check Cloud Logging /{" "}
          <code>lighter-mm run-status</code>.
        </p>
      </section>
    );
  }

  const target = overview.observation_target_hours;
  const obs = overview.observation_hours;
  const top = overview.top_candidate;
  const warnings = markets
    .flatMap((m) => (m.warnings || []).map((w) => ({ symbol: m.symbol, w })))
    .slice(0, 12);

  return (
    <>
      <section className="card">
        <div className="grid">
          <div className="kpi">
            <div className="label">Status</div>
            <div className={`value status-${overview.status}`}>{overview.status}</div>
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
          </div>
          <div className="kpi">
            <div className="label">Markets</div>
            <div className="value">{overview.markets}</div>
          </div>
          <div className="kpi">
            <div className="label">Coverage</div>
            <div className="value">
              {overview.coverage_pct != null ? `${overview.coverage_pct.toFixed(1)}%` : "—"}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Last Update</div>
            <div className="value" style={{ fontSize: "0.95rem" }}>
              {overview.last_update
                ? new Date(overview.last_update).toLocaleString()
                : "—"}
            </div>
          </div>
        </div>
        <p className="muted" style={{ marginTop: "0.8rem" }}>
          git {overview.git_sha || "unknown"} · collector {overview.collector_version || "?"} ·
          generated {overview.generated_at}
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
                <th>TPM</th>
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
                    <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                    <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
                    <td>{fmt(m.current_funding_rate, 4)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {!markets.some((m) => m.is_candidate) && (
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
