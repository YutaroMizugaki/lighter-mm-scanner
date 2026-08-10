"use client";

import EstimatedFillValue from "@/components/EstimatedFillValue";
import QualityChip from "@/components/QualityChip";
import RankBadge from "@/components/RankBadge";
import ScoreBar from "@/components/ScoreBar";
import SignedValue from "@/components/SignedValue";
import { fmt } from "@/lib/format";
import {
  ESTIMATED_EDGE_TOOLTIP,
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import { formatActivity, formatDepth, TOOLTIPS } from "@/lib/public";
import type { MarketRow } from "@/lib/types";
import Link from "next/link";
import { useMemo, useState } from "react";

export default function MarketsClient({ markets }: { markets: MarketRow[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<
    "score" | "spread" | "fill30" | "tpm" | "tpm_avg" | "m5" | "edge30"
  >("score");
  const [candidatesOnly, setCandidatesOnly] = useState(false);

  const rows = useMemo(() => {
    let xs = [...markets];
    if (candidatesOnly) xs = xs.filter((m) => m.is_candidate);
    if (q.trim()) {
      const qq = q.trim().toLowerCase();
      xs = xs.filter((m) => m.symbol.toLowerCase().includes(qq));
    }
    xs.sort((a, b) => {
      const pick = (m: MarketRow) => {
        if (sort === "score") return m.score;
        if (sort === "spread") return m.median_spread_bps || 0;
        if (sort === "fill30") return m.estimated_maker_fill_rate_30s_conservative ?? -1;
        if (sort === "tpm") return m.trades_per_minute_median || 0;
        if (sort === "tpm_avg") return m.trades_per_minute_mean || 0;
        if (sort === "edge30") return m.estimated_maker_edge_30s_bps ?? -999;
        return m.maker_markout_5s_median_bps || 0;
      };
      return pick(b) - pick(a);
    });
    return xs;
  }, [markets, q, sort, candidatesOnly]);

  return (
    <>
      <div className="controls">
        <input
          placeholder="Search symbol"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search symbol"
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as typeof sort)}
          aria-label="Sort markets"
        >
          <option value="score">Sort: Score</option>
          <option value="spread">Sort: Spread</option>
          <option value="fill30">Sort: Est. Fill 30s</option>
          <option value="edge30">Sort: Est. Maker Edge</option>
          <option value="tpm">Sort: TPM median</option>
          <option value="tpm_avg">Sort: TPM avg</option>
          <option value="m5">Sort: Markout 5s</option>
        </select>
        <label className="muted">
          <input
            type="checkbox"
            checked={candidatesOnly}
            onChange={(e) => setCandidatesOnly(e.target.checked)}
          />{" "}
          Candidates only
        </label>
      </div>
      <p className="muted" style={{ marginTop: 0, maxWidth: 920 }} title={ESTIMATED_FILL_TOOLTIP}>
        Est. Fill = Estimated Maker Fill ($50 / 30s / Conservative). Depth and TPM are market
        activity / liquidity — not fill probability.
      </p>
      <div className="table-scroll" style={{ maxHeight: 640 }}>
        <table className="market-table">
          <thead>
            <tr>
              <th className="sticky-col">Market</th>
              <th>Rank</th>
              <th title={TOOLTIPS.score}>Score</th>
              <th title={ESTIMATED_FILL_TOOLTIP}>Est. Fill</th>
              <th>Spread</th>
              <th title={TOOLTIPS.depth10bp}>Depth</th>
              <th title={TOOLTIPS.tradesPerMin}>Activity</th>
              <th title={TOOLTIPS.makerMarkout}>M5</th>
              <th title={TOOLTIPS.makerMarkout}>M30</th>
              <th title={ESTIMATED_EDGE_TOOLTIP}>Est. Edge</th>
              <th title={TOOLTIPS.coverage}>Coverage</th>
              <th title={TOOLTIPS.sampleQuality}>Quality</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
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
                <td className="tabular" title={TOOLTIPS.depth10bp}>
                  {formatDepth(m.median_two_sided_depth_10bps_usd)}
                </td>
                <td className="tabular" title={TOOLTIPS.tradesPerMin}>
                  {formatActivity(m.trades_per_minute_median)}
                </td>
                <td>
                  <SignedValue value={m.maker_markout_5s_median_bps} />
                </td>
                <td>
                  <SignedValue value={m.maker_markout_30s_median_bps} />
                </td>
                <td title={ESTIMATED_EDGE_TOOLTIP}>
                  <span className="tabular">
                    <SignedValue value={m.estimated_maker_edge_30s_bps} />
                  </span>
                  {m.estimated_maker_edge_fee_included === false && (
                    <span className="edge-meta">fee excl.</span>
                  )}
                </td>
                <td className="tabular" title={TOOLTIPS.coverage}>
                  {m.data_coverage_pct != null ? `${fmt(m.data_coverage_pct, 1)}%` : "—"}
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
    </>
  );
}
