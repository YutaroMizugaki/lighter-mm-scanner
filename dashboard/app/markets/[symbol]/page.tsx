import Link from "next/link";
import {
  ESTIMATED_FILL_TOOLTIP,
  fmt,
  fmtEstimatedFill,
  fmtSampleQuality,
  getMarket,
} from "@/lib/data";

function sizeCell(
  bySize: Record<string, Record<string, { conservative?: number | null; optimistic?: number | null }>> | null | undefined,
  size: string,
  horizon: string,
  mode: "conservative" | "optimistic",
  quality?: string | null,
): string {
  const rate = bySize?.[size]?.[horizon]?.[mode];
  return fmtEstimatedFill(rate, quality);
}

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
  const bySize = m.estimated_maker_fill_by_size;
  const fillQ = m.estimated_maker_fill_sample_quality;
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
        <div className="kpi" title={ESTIMATED_FILL_TOOLTIP}>
          <div className="label">Est. Fill 30s ($50 cons.)</div>
          <div className="value">
            {fmtEstimatedFill(m.estimated_maker_fill_rate_30s_conservative, fillQ)}
          </div>
        </div>
        <div className="kpi" title={ESTIMATED_FILL_TOOLTIP}>
          <div className="label">Est. Fill 5s ($50 cons.)</div>
          <div className="value">
            {fmtEstimatedFill(m.estimated_maker_fill_rate_5s_conservative, fillQ)}
          </div>
        </div>
        <div className="kpi">
          <div className="label">Est. Maker Edge 30s</div>
          <div className="value">{fmt(m.estimated_maker_edge_30s_bps, 2, true)} bp</div>
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
          <div className="label">Fill Sample Quality</div>
          <div className="value">{fmtSampleQuality(fillQ)}</div>
        </div>
        <div className="kpi">
          <div className="label">Markout Sample Quality</div>
          <div className="value">{fmtSampleQuality(m.markout_sample_quality)}</div>
        </div>
        <div className="kpi">
          <div className="label">Depth ±10bp</div>
          <div className="value">${fmt(m.median_two_sided_depth_10bps_usd, 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">TPM median</div>
          <div className="value">{fmt(m.trades_per_minute_median)}</div>
        </div>
        <div className="kpi" title="Market-level trade prints (not Estimated Maker Fill)">
          <div className="label">Trades (market-level)</div>
          <div className="value">{fmt(m.total_trade_count, 0)}</div>
        </div>
        <div className="kpi">
          <div className="label">Coverage</div>
          <div className="value">{fmt(m.data_coverage_pct, 1)}%</div>
        </div>
      </div>

      <h3 title={ESTIMATED_FILL_TOOLTIP}>Estimated Maker Fill by size</h3>
      <p className="muted" style={{ maxWidth: 860 }}>
        Ranking default is <strong>$50</strong> conservative. Optimistic assumes near front of
        queue; conservative assumes the full displayed touch size is ahead. Not actual fill
        probability.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Size</th>
              <th>5s cons.</th>
              <th>5s opt.</th>
              <th>30s cons.</th>
              <th>30s opt.</th>
            </tr>
          </thead>
          <tbody>
            {["25", "50", "100"].map((size) => (
              <tr key={size}>
                <td>
                  ${size}
                  {size === "50" ? " (ranking)" : ""}
                </td>
                <td>{sizeCell(bySize, size, "5s", "conservative", fillQ)}</td>
                <td>{sizeCell(bySize, size, "5s", "optimistic", fillQ)}</td>
                <td>{sizeCell(bySize, size, "30s", "conservative", fillQ)}</td>
                <td>{sizeCell(bySize, size, "30s", "optimistic", fillQ)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!bySize && (
        <p className="muted">
          Size ladder unavailable (older analyzer JSON). Top-level $50 fields still show above when
          present.
        </p>
      )}

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
