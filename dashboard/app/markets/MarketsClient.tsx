"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { MarketRow } from "@/lib/data";
import {
  ESTIMATED_FILL_TOOLTIP,
  fmt,
  fmtEstimatedFill,
  fmtSampleQuality,
} from "@/lib/data";

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
        <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
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
        Est. Fill = Estimated Maker Fill ($50 conservative). Hover column header for definition.
        Market-level Trades/TPM are activity, not fill.
      </p>
      <div style={{ overflowX: "auto", maxHeight: 640 }}>
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
              <th title="Estimated Maker Edge (not expected profit)">Est. Edge</th>
              <th>Sample Q</th>
              <th title="Market-level trade prints (not Estimated Maker Fill)">Trades</th>
              <th>Coverage</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
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
                <td title={ESTIMATED_FILL_TOOLTIP}>
                  {fmtEstimatedFill(
                    m.estimated_maker_fill_rate_30s_conservative,
                    m.estimated_maker_fill_sample_quality,
                  )}
                </td>
                <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
                <td>{fmt(m.estimated_maker_edge_30s_bps, 2, true)}</td>
                <td>
                  {fmtSampleQuality(
                    m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality,
                  )}
                </td>
                <td>{fmt(m.total_trade_count, 0)}</td>
                <td>{fmt(m.data_coverage_pct, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
