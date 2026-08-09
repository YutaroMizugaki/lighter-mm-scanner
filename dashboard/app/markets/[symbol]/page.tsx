import Link from "next/link";
import { fmt, getMarket } from "@/lib/data";

export default async function MarketDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const m = await getMarket(symbol);
  if (!m) {
    return (
      <section className="card">
        <h2>{symbol}</h2>
        <p className="muted">Detail JSON not found yet.</p>
        <Link href="/markets">← All markets</Link>
      </section>
    );
  }
  return (
    <section className="card">
      <p>
        <Link href="/markets">← All markets</Link>
      </p>
      <h2>
        {m.symbol}{" "}
        <span className={`badge ${m.letter_rank}`}>{m.letter_rank}</span> · {fmt(m.score, 1)}
      </h2>
      <div className="grid">
        <div className="kpi">
          <div className="label">Median Spread</div>
          <div className="value">{fmt(m.median_spread_bps)} bp</div>
        </div>
        <div className="kpi">
          <div className="label">Depth ±10bp</div>
          <div className="value">${fmt(m.median_two_sided_depth_10bps_usd, 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">TPM median</div>
          <div className="value">{fmt(m.trades_per_minute_median)}</div>
        </div>
        <div className="kpi">
          <div className="label">TPM avg</div>
          <div className="value">{fmt(m.trades_per_minute_mean)}</div>
        </div>
        <div className="kpi">
          <div className="label">Trades</div>
          <div className="value">{fmt(m.total_trade_count, 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">Markout 5s</div>
          <div className="value">{fmt(m.maker_markout_5s_median_bps, 2, true)} bp</div>
        </div>
        <div className="kpi">
          <div className="label">Markout 30s</div>
          <div className="value">{fmt(m.maker_markout_30s_median_bps, 2, true)} bp</div>
        </div>
        <div className="kpi">
          <div className="label">Funding</div>
          <div className="value">{fmt(m.current_funding_rate, 4)}</div>
        </div>
        <div className="kpi">
          <div className="label">Coverage</div>
          <div className="value">{fmt(m.data_coverage_pct, 1)}%</div>
        </div>
      </div>
      <h3>Pros</h3>
      <ul className="compact">
        {(m.pros || []).map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
      <h3>Cons</h3>
      <ul className="compact">
        {(m.cons || []).length ? (m.cons || []).map((p) => <li key={p}>{p}</li>) : <li>none</li>}
      </ul>
      <h3>Warnings</h3>
      <ul className="compact">
        {(m.warnings || []).length ? (
          (m.warnings || []).map((p) => <li key={p}>{p}</li>)
        ) : (
          <li>none</li>
        )}
      </ul>
    </section>
  );
}
