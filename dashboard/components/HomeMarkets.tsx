"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { MarketRow } from "@/lib/types";
import { fmt, fmtEstimatedFill } from "@/lib/format";
import { formatActivity, formatDepth, TOOLTIPS } from "@/lib/public";
import { ESTIMATED_FILL_TOOLTIP } from "@/lib/marketMetrics";
import QualityChip from "./QualityChip";
import SignedValue from "./SignedValue";
import styles from "./HomeMarkets.module.css";

const PAGE_SIZE = 12;
const SORTS = {
  score: "総合スコア順",
  spread: "スプレッド順",
  depth: "板の厚さ順",
  activity: "取引回数順",
} as const;
type Sort = keyof typeof SORTS;

function sortValue(m: MarketRow, sort: Sort): number {
  const values = {
    score: m.effective_score ?? m.score,
    spread: m.median_spread_bps,
    depth: m.median_two_sided_depth_10bps_usd,
    activity: m.trades_per_minute_median,
  };
  return values[sort] ?? -Infinity;
}

function isScreened(m: MarketRow): boolean {
  return m.analysis_stage === "screened" || m.analysis_stage === "selected_incomplete";
}

function SymbolName({ market }: { market: MarketRow }) {
  return isScreened(market) ? <span>{market.symbol}</span> : (
    <Link href={`/markets/${encodeURIComponent(market.symbol)}`}>{market.symbol}</Link>
  );
}

function marketMetrics(m: MarketRow) {
  return [
    { label: "スプレッド", value: `${fmt(m.median_spread_bps)} bp` },
    { label: "板の厚さ ±10bp", value: formatDepth(m.median_two_sided_depth_10bps_usd), title: TOOLTIPS.depth10bp },
    { label: "取引回数 / 分", value: formatActivity(m.trades_per_minute_median), title: TOOLTIPS.tradesPerMin },
    { label: "総取引回数", value: m.total_trade_count == null ? "—" : m.total_trade_count.toLocaleString("ja-JP"), title: "表示中の分析対象期間内に観測した取引回数です。" },
    { label: "推定約定率 · 30秒", value: fmtEstimatedFill(m.estimated_maker_fill_rate_30s_conservative, m.estimated_maker_fill_sample_quality), title: ESTIMATED_FILL_TOOLTIP },
    { label: "約定30秒後", value: <SignedValue value={m.maker_markout_30s_median_bps} />, title: TOOLTIPS.makerMarkout },
    { label: "資金調達率（推定）", value: m.current_funding_rate == null ? "—" : `${fmt(m.current_funding_rate, 4, true)}%`, title: "観測時点の次回資金調達率の推定値です。" },
    { label: "データ網羅率", value: m.data_coverage_pct == null ? "—" : `${fmt(m.data_coverage_pct, 1)}%`, title: TOOLTIPS.coverage },
  ];
}

export default function HomeMarkets({ markets }: { markets: MarketRow[] }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("score");
  const [candidatesOnly, setCandidatesOnly] = useState(false);
  const [view, setView] = useState<"cards" | "table">("cards");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return markets
      .filter((m) => (!candidatesOnly || m.is_candidate) && m.symbol.toLowerCase().includes(q))
      .sort((a, b) => {
        const av = sortValue(a, sort);
        const bv = sortValue(b, sort);
        return av === bv ? a.symbol.localeCompare(b.symbol) : bv - av;
      });
  }, [markets, query, sort, candidatesOnly]);
  const visible = rows.slice(0, limit);

  return (
    <section className={styles.section} aria-label="銘柄の比較">
      <div className={styles.controls}>
        <input type="search" placeholder="銘柄を検索" aria-label="銘柄を検索" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE_SIZE); }} />
        <select aria-label="並べ替え" value={sort} onChange={(e) => { setSort(e.target.value as Sort); setLimit(PAGE_SIZE); }}>
          {Object.entries(SORTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label className={styles.filter}>
          <input type="checkbox" checked={candidatesOnly} onChange={(e) => { setCandidatesOnly(e.target.checked); setLimit(PAGE_SIZE); }} />候補のみ
        </label>
        <div className={`view-toggle ${styles.toggle}`} role="group" aria-label="表示形式">
          <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")}>カード</button>
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>表</button>
        </div>
      </div>
      <p className={styles.resultCount} role="status">{visible.length} / {rows.length} 銘柄</p>
      {rows.length === 0 ? (
        <p className={styles.empty}>{markets.length === 0 ? "最新の分析に表示できる銘柄はありません。" : "条件に一致する銘柄はありません。"}</p>
      ) : view === "cards" ? (
        <div className={styles.grid}>
          {visible.map((m) => (
            <article key={m.symbol} className={styles.card} aria-label={m.symbol}>
              <header className={styles.cardHeader}>
                <div className={styles.identity}>
                  <h2><SymbolName market={m} /></h2>
                  <div className={styles.badges}>
                    {m.is_candidate && <span className={styles.candidate}>候補</span>}
                    {isScreened(m) ? <span className="muted">{m.analysis_stage === "selected_incomplete" ? "分析未完了" : "簡易分析"}</span> : <QualityChip quality={m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality} />}
                  </div>
                </div>
                <div className={styles.score} title={TOOLTIPS.effectiveScore}>
                  <span>総合スコア</span>
                  <strong className="tabular">{fmt(m.effective_score ?? m.score, 1)}</strong>
                </div>
              </header>
              <dl className={styles.metrics}>
                {marketMetrics(m).map((metric) => (
                  <div key={metric.label} title={metric.title}>
                    <dt>{metric.label}</dt>
                    <dd className="tabular">{metric.value}</dd>
                  </div>
                ))}
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <div className={`table-scroll ${styles.tableScroll}`} tabIndex={0} role="region" aria-label="銘柄比較表（横にスクロールできます）">
          <table className={`market-table ${styles.table}`}>
            <thead><tr>
              <th scope="col" className="sticky-col">銘柄</th>
              <th scope="col" title={TOOLTIPS.effectiveScore}>総合スコア</th>
              {marketMetrics(visible[0]).map((metric) => <th scope="col" key={metric.label} title={metric.title}>{metric.label}</th>)}
              <th scope="col" title={TOOLTIPS.sampleQuality}>データ品質</th>
            </tr></thead>
            <tbody>{visible.map((m) => <tr key={m.symbol}>
              <th scope="row" className="sticky-col"><SymbolName market={m} />{m.is_candidate && <span className={styles.candidate}>候補</span>}{isScreened(m) && <span className={styles.stage}>{m.analysis_stage === "selected_incomplete" ? "分析未完了" : "簡易分析"}</span>}</th>
              <td className="tabular">{fmt(m.effective_score ?? m.score, 1)}</td>
              {marketMetrics(m).map((metric) => <td key={metric.label} className="tabular">{metric.value}</td>)}
              <td><QualityChip quality={m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality} /></td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
      {rows.length > visible.length && <div className={styles.more}><button type="button" className="btn" onClick={() => setLimit((n) => n + PAGE_SIZE)}>さらに{Math.min(PAGE_SIZE, rows.length - visible.length)}銘柄を表示</button></div>}
    </section>
  );
}
