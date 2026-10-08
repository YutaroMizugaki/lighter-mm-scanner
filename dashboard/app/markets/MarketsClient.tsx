"use client";

import EstimatedFillValue from "@/components/EstimatedFillValue";
import QualityChip from "@/components/QualityChip";
import RankBadge from "@/components/RankBadge";
import ScoreBar from "@/components/ScoreBar";
import SignedValue from "@/components/SignedValue";
import { fmt, fmtPaperCount, fmtPaperUsd, fmtPctFraction } from "@/lib/format";
import {
  ESTIMATED_EDGE_TOOLTIP,
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import { formatActivity, formatDepth, TOOLTIPS } from "@/lib/public";
import type { MarketRow } from "@/lib/types";
import Link from "next/link";
import { useMemo, useState } from "react";

function isScreened(m: MarketRow): boolean {
  return m.analysis_stage === "screened" || m.analysis_stage === "selected_incomplete";
}

function analysisBadge(m: MarketRow): { label: string; kind: "screened" | "full" } {
  if (m.analysis_stage === "selected_incomplete") {
    return { label: "分析未完了", kind: "screened" };
  }
  if (m.analysis_stage === "screened") {
    return { label: "簡易分析", kind: "screened" };
  }
  return { label: "詳細分析", kind: "full" };
}

export default function MarketsClient({ markets }: { markets: MarketRow[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<
    | "effective"
    | "score"
    | "spread"
    | "fill30"
    | "tpm"
    | "tpm_avg"
    | "m5"
    | "edge30"
  >("effective");
  const [candidatesOnly, setCandidatesOnly] = useState(false);
  const [view, setView] = useState<"basic" | "detail">("basic");

  const rows = useMemo(() => {
    let xs = [...markets];
    if (candidatesOnly) xs = xs.filter((m) => m.is_candidate);
    if (q.trim()) {
      const qq = q.trim().toLowerCase();
      xs = xs.filter((m) => m.symbol.toLowerCase().includes(qq));
    }
    xs.sort((a, b) => {
      const pick = (m: MarketRow) => {
        if (sort === "effective") return m.effective_score ?? m.score ?? -Infinity;
        if (sort === "score") return m.score ?? -Infinity;
        if (sort === "spread") return m.median_spread_bps ?? -Infinity;
        if (sort === "fill30") return m.estimated_maker_fill_rate_30s_conservative ?? -1;
        if (sort === "tpm") return m.trades_per_minute_median ?? -Infinity;
        if (sort === "tpm_avg") return m.trades_per_minute_mean ?? -Infinity;
        if (sort === "edge30") return m.estimated_maker_edge_30s_bps ?? -999;
        return m.maker_markout_5s_median_bps ?? -Infinity;
      };
      return pick(b) - pick(a);
    });
    return xs;
  }, [markets, q, sort, candidatesOnly]);

  return (
    <>
      <div className="controls">
        <input
          placeholder="銘柄を検索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="銘柄を検索"
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as typeof sort)}
          aria-label="並べ替え"
        >
          <option value="effective">総合スコア順</option>
          <option value="score">スコア順</option>
          <option value="spread">スプレッド順</option>
          <option value="fill30">約定シミュレーション（30秒）順</option>
          <option value="edge30">推定エッジ順</option>
          <option value="tpm">取引回数（中央値）順</option>
          <option value="tpm_avg">取引回数（平均）順</option>
          <option value="m5">5秒後の価格変化順</option>
        </select>
        <label className="muted">
          <input
            type="checkbox"
            checked={candidatesOnly}
            onChange={(e) => setCandidatesOnly(e.target.checked)}
          />{" "}
          候補のみ
        </label>
        <div className="view-toggle" role="group" aria-label="表示項目">
          <button type="button" aria-pressed={view === "basic"} onClick={() => setView("basic")}>
            基本
          </button>
          <button type="button" aria-pressed={view === "detail"} onClick={() => setView("detail")}>
            詳細
          </button>
        </div>
      </div>
      <p className="muted" style={{ marginTop: 0 }} aria-live="polite">
        {rows.length}市場を表示中
      </p>
      <p className="muted" style={{ marginTop: 0, maxWidth: 920 }} title={ESTIMATED_FILL_TOOLTIP}>
        約定シミュレーションは「指値50ドル・30秒・保守的条件」の推定値です。板の厚さや取引回数は市場の流動性・活発さを示し、約定確率ではありません。
      </p>
      <div className="table-scroll" style={{ maxHeight: 640 }} tabIndex={0} aria-label="市場一覧。横にスクロールできます">
        <table className={`market-table ${view === "basic" ? "basic-view" : "detail-view"}`}>
          <thead>
            <tr>
              <th className="sticky-col">銘柄</th>
              <th className="extended-col">分析</th>
              <th className="extended-col">評価</th>
              <th className="extended-col" title={TOOLTIPS.score}>スコア</th>
              <th className="extended-col" title={TOOLTIPS.confidence}>データ信頼度</th>
              <th title={TOOLTIPS.effectiveScore}>総合スコア</th>
              <th title={ESTIMATED_FILL_TOOLTIP}>約定シミュレーション</th>
              <th>スプレッド</th>
              <th title={TOOLTIPS.depth10bp}>板の厚さ</th>
              <th className="extended-col" title={TOOLTIPS.tradesPerMin}>取引回数/分</th>
              <th className="extended-col" title={TOOLTIPS.makerMarkout}>5秒後</th>
              <th className="extended-col" title={TOOLTIPS.makerMarkout}>30秒後</th>
              <th className="extended-col" title={ESTIMATED_EDGE_TOOLTIP}>推定エッジ</th>
              <th className="extended-col" title={TOOLTIPS.coverage}>データ網羅率</th>
              <th title={TOOLTIPS.sampleQuality}>データ品質</th>
              <th className="paper-mm-col extended-col">仮想損益</th>
              <th className="paper-mm-col extended-col">往復約定数</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.symbol}>
                <td className="sticky-col">
                  {isScreened(m) ? (
                    <span>{m.symbol}</span>
                  ) : (
                    <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
                  )}
                </td>
                <td className="extended-col">
                  {(() => {
                    const badge = analysisBadge(m);
                    return (
                    <span className={`badge analysis-badge ${badge.kind}`}>{badge.label}</span>
                    );
                  })()}
                </td>
                <td className="extended-col">
                  <RankBadge letter={m.letter_rank} />
                </td>
                <td className="extended-col">
                  <ScoreBar score={m.score} />
                </td>
                <td className="tabular extended-col" title={TOOLTIPS.confidence}>
                  {fmtPctFraction(m.confidence, 0)}
                </td>
                <td className="tabular" title={TOOLTIPS.effectiveScore}>
                  {fmt(m.effective_score ?? m.score, 1)}
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
                <td className="tabular extended-col" title={TOOLTIPS.tradesPerMin}>
                  {formatActivity(m.trades_per_minute_median)}
                </td>
                <td className="extended-col">
                  <SignedValue value={m.maker_markout_5s_median_bps} />
                </td>
                <td className="extended-col">
                  <SignedValue value={m.maker_markout_30s_median_bps} />
                </td>
                <td className="extended-col" title={ESTIMATED_EDGE_TOOLTIP}>
                  <span className="tabular">
                    <SignedValue value={m.estimated_maker_edge_30s_bps} />
                  </span>
                  {m.estimated_maker_edge_fee_included === false && (
                    <span className="edge-meta">手数料別</span>
                  )}
                </td>
                <td className="tabular extended-col" title={TOOLTIPS.coverage}>
                  {m.data_coverage_pct != null ? `${fmt(m.data_coverage_pct, 1)}%` : "—"}
                </td>
                <td>
                  <QualityChip
                    quality={
                      m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality
                    }
                  />
                </td>
                <td className="tabular paper-mm-col extended-col">
                  {fmtPaperUsd(m.paper_mm_total_pnl_usd, m.paper_mm_status, true)}
                </td>
                <td className="tabular paper-mm-col extended-col">
                  {fmtPaperCount(m.paper_mm_round_trips, m.paper_mm_status)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={17} className="muted">条件に一致する市場はありません。</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
