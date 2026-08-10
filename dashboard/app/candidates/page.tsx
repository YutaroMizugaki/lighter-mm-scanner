import Link from "next/link";
import {
  ESTIMATED_FILL_TOOLTIP,
  fmt,
  fmtEstimatedFill,
  fmtSampleQuality,
  getMarkets,
} from "@/lib/data";

export default async function CandidatesPage() {
  const markets = (await getMarkets()).filter((m) => m.is_candidate);
  return (
    <section className="card">
      <h2>MM Candidates</h2>
      <p className="muted" title={ESTIMATED_FILL_TOOLTIP}>
        Est. Fill is Estimated Maker Fill ($50 / 30s / conservative), not market-level trade count.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Symbol</th>
              <th>Rank</th>
              <th>Score</th>
              <th>Spread</th>
              <th title={ESTIMATED_FILL_TOOLTIP}>Est. Fill 30s</th>
              <th>M5</th>
              <th>M30</th>
              <th>Est. Edge</th>
              <th>Sample Q</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m, i) => (
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
                  {fmtEstimatedFill(
                    m.estimated_maker_fill_rate_30s_conservative,
                    m.estimated_maker_fill_sample_quality,
                  )}
                </td>
                <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
                <td>{fmt(m.estimated_maker_edge_30s_bps, 2, true)}</td>
                <td>{fmtSampleQuality(m.estimated_maker_fill_sample_quality)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!markets.length && <p className="muted">No candidates in latest aggregates.</p>}
    </section>
  );
}
