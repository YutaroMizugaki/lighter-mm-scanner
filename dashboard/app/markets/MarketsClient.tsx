"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { MarketRow } from "@/lib/data";
import { fmt } from "@/lib/data";

export default function MarketsClient({ markets }: { markets: MarketRow[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"score" | "spread" | "tpm" | "m5">("score");
  const [candidatesOnly, setCandidatesOnly] = useState(false);

  const rows = useMemo(() => {
    let xs = [...markets];
    if (candidatesOnly) xs = xs.filter((m) => m.is_candidate);
    if (q.trim()) {
      const qq = q.trim().toLowerCase();
      xs = xs.filter((m) => m.symbol.toLowerCase().includes(qq));
    }
    xs.sort((a, b) => {
      const av =
        sort === "score"
          ? a.score
          : sort === "spread"
            ? a.median_spread_bps || 0
            : sort === "tpm"
              ? a.trades_per_minute_median || 0
              : a.maker_markout_5s_median_bps || 0;
      const bv =
        sort === "score"
          ? b.score
          : sort === "spread"
            ? b.median_spread_bps || 0
            : sort === "tpm"
              ? b.trades_per_minute_median || 0
              : b.maker_markout_5s_median_bps || 0;
      return bv - av;
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
          <option value="tpm">Sort: Trades/min</option>
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
      <div style={{ overflowX: "auto", maxHeight: 640 }}>
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
                <td>{fmt(m.median_two_sided_depth_10bps_usd, 0)}</td>
                <td>{fmt(m.trades_per_minute_median)}</td>
                <td>{fmt(m.maker_markout_5s_median_bps, 2, true)}</td>
                <td>{fmt(m.maker_markout_30s_median_bps, 2, true)}</td>
                <td>{fmt(m.data_coverage_pct, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
