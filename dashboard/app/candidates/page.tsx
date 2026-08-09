import Link from "next/link";
import { fmt, getMarkets } from "@/lib/data";

export default async function CandidatesPage() {
  const markets = (await getMarkets()).filter((m) => m.is_candidate);
  return (
    <section className="card">
      <h2>MM Candidates</h2>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Symbol</th>
              <th>Rank</th>
              <th>Score</th>
              <th>Spread</th>
              <th>Depth10</th>
              <th>TPM</th>
              <th>M5</th>
              <th>M30</th>
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
                <td>{fmt(m.median_two_sided_depth_10bps_usd, 0)}</td>
                <td>{fmt(m.trades_per_minute_median)}</td>
                <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!markets.length && <p className="muted">No candidates in latest aggregates.</p>}
    </section>
  );
}
