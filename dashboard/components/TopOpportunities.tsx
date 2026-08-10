import Link from "next/link";
import type { MarketRow } from "@/lib/types";
import { fmt } from "@/lib/format";
import { formatDepth, TOOLTIPS } from "@/lib/public";
import EstimatedFillValue from "./EstimatedFillValue";
import QualityChip from "./QualityChip";
import RankBadge from "./RankBadge";
import ScoreBar from "./ScoreBar";
import SignedValue from "./SignedValue";

type Props = {
  candidates: MarketRow[];
  fetchFailed?: boolean;
};

export default function TopOpportunities({ candidates, fetchFailed = false }: Props) {
  const top = candidates.slice(0, 5);

  return (
    <section className="panel" aria-labelledby="top-opps-heading">
      <div className="section-header">
        <h2 id="top-opps-heading">Top Opportunities</h2>
        <p className="section-lead">
          Estimated Fill uses $50 / 30s / Conservative. Rankings are research signals, not trade
          recommendations.
        </p>
      </div>

      {fetchFailed ? (
        <p className="muted">Market aggregate data could not be loaded.</p>
      ) : top.length === 0 ? (
        <>
          <p>No markets currently meet all candidate thresholds.</p>
          <p>
            <Link href="/markets">Explore all markets →</Link>
          </p>
        </>
      ) : (
        <>
          <div className="table-scroll">
            <table className="market-table top-opps-table">
              <thead>
                <tr>
                  <th className="sticky-col">Market</th>
                  <th>Rank</th>
                  <th>Score</th>
                  <th title={TOOLTIPS.estimatedFill}>Est. Fill</th>
                  <th>Spread</th>
                  <th title={TOOLTIPS.makerMarkout}>M5</th>
                  <th title={TOOLTIPS.makerMarkout}>M30</th>
                  <th title={TOOLTIPS.depth10bp}>Depth</th>
                  <th title={TOOLTIPS.sampleQuality}>Quality</th>
                </tr>
              </thead>
              <tbody>
                {top.map((m) => (
                  <tr key={m.symbol}>
                    <td className="sticky-col">
                      <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>
                        {m.symbol}
                      </Link>
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
                    <td>
                      <SignedValue value={m.maker_markout_5s_median_bps} />
                    </td>
                    <td>
                      <SignedValue value={m.maker_markout_30s_median_bps} />
                    </td>
                    <td className="tabular" title={TOOLTIPS.depth10bp}>
                      {formatDepth(m.median_two_sided_depth_10bps_usd)}
                    </td>
                    <td>
                      <QualityChip
                        quality={
                          m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="section-footer-link">
            <Link href="/candidates">View all candidates →</Link>
            {" · "}
            <Link href="/markets">Explore all markets →</Link>
          </p>
        </>
      )}
    </section>
  );
}
