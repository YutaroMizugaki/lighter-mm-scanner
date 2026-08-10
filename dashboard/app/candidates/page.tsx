import EstimatedFillValue from "@/components/EstimatedFillValue";
import PublicErrorState from "@/components/PublicErrorState";
import QualityChip from "@/components/QualityChip";
import RankBadge from "@/components/RankBadge";
import ScoreBar from "@/components/ScoreBar";
import SignedValue from "@/components/SignedValue";
import { getMarketsResult } from "@/lib/api";
import { fmt } from "@/lib/format";
import {
  ESTIMATED_EDGE_TOOLTIP,
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import { formatDepth, publicDataUnavailableMessage, TOOLTIPS } from "@/lib/public";
import Link from "next/link";

export default async function CandidatesPage() {
  const result = await getMarketsResult();

  if (!result.ok) {
    const msg = publicDataUnavailableMessage("markets");
    return <PublicErrorState title={msg.title} body={msg.body} />;
  }

  const markets = (result.data.markets ?? []).filter((m) => m.is_candidate);

  return (
    <section className="panel">
      <div className="section-header">
        <h1 style={{ margin: 0, fontSize: "1.35rem" }}>Candidates</h1>
        <p className="section-lead" title={ESTIMATED_FILL_TOOLTIP}>
          Markets that currently meet all candidate thresholds. Est. Fill is Estimated Maker Fill
          ($50 / 30s / Conservative), not market-level trade count.
        </p>
      </div>
      {markets.length === 0 ? (
        <>
          <p>No markets currently meet all candidate thresholds.</p>
          <p>
            <Link href="/markets">Explore all markets →</Link>
          </p>
        </>
      ) : (
        <div className="table-scroll">
          <table className="market-table">
            <thead>
              <tr>
                <th className="sticky-col">Market</th>
                <th>Rank</th>
                <th title={TOOLTIPS.score}>Score</th>
                <th title={ESTIMATED_FILL_TOOLTIP}>Est. Fill</th>
                <th>Spread</th>
                <th title={TOOLTIPS.depth10bp}>Depth</th>
                <th title={TOOLTIPS.makerMarkout}>M5</th>
                <th title={TOOLTIPS.makerMarkout}>M30</th>
                <th title={ESTIMATED_EDGE_TOOLTIP}>Est. Edge</th>
                <th title={TOOLTIPS.sampleQuality}>Quality</th>
              </tr>
            </thead>
            <tbody>
              {markets.map((m) => (
                <tr key={m.symbol}>
                  <td className="sticky-col">
                    <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
                  </td>
                  <td>
                    <RankBadge letter={m.letter_rank} />
                  </td>
                  <td>
                    <ScoreBar score={m.score} />
                  </td>
                  <td>
                    <EstimatedFillValue
                      rate={m.estimated_maker_fill_rate_30s_conservative}
                      quality={m.estimated_maker_fill_sample_quality}
                      compact
                    />
                  </td>
                  <td className="tabular">
                    {fmt(m.median_spread_bps)}
                    <span className="unit"> bp</span>
                  </td>
                  <td className="tabular">{formatDepth(m.median_two_sided_depth_10bps_usd)}</td>
                  <td>
                    <SignedValue value={m.maker_markout_5s_median_bps} />
                  </td>
                  <td>
                    <SignedValue value={m.maker_markout_30s_median_bps} />
                  </td>
                  <td title={ESTIMATED_EDGE_TOOLTIP}>
                    <SignedValue value={m.estimated_maker_edge_30s_bps} />
                    {m.estimated_maker_edge_fee_included === false && (
                      <span className="edge-meta">fee excl.</span>
                    )}
                  </td>
                  <td>
                    <QualityChip quality={m.estimated_maker_fill_sample_quality} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
