"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import type { MarketRow } from "@/lib/types";
import { fmt, fmtEstimatedFill, fmtSampleQuality } from "@/lib/format";
import { formatDepth, TOOLTIPS } from "@/lib/public";
import { ESTIMATED_FILL_TOOLTIP, ESTIMATED_EDGE_TOOLTIP } from "@/lib/marketMetrics";
import SignedValue from "./SignedValue";
import { homeOpportunityPriority, HOME_RANKING_TOOLTIP } from "@/lib/homeRanking";
import styles from "./HomeMarkets.module.css";

const PAGE_SIZE = 20;
const SORTS = { opportunity: "鞘・流動性のバランス順", score: "総合スコア順", spread: "スプレッド順", depth: "板の厚さ順", activity: "取引回数順", fill: "推定約定率順", markout: "約定30秒後の価格変化順" } as const;
type Sort = keyof typeof SORTS;
function sortValue(m: MarketRow, sort: Sort): number {
  const values = { opportunity: homeOpportunityPriority(m), score: m.effective_score ?? m.score, spread: m.median_spread_bps, depth: m.median_two_sided_depth_10bps_usd, activity: m.trades_per_minute_median, fill: m.estimated_maker_fill_sample_quality === "insufficient" ? null : m.estimated_maker_fill_rate_30s_conservative, markout: m.maker_markout_30s_median_bps };
  return values[sort] ?? -Infinity;
}
function isScreened(m: MarketRow): boolean { return m.analysis_stage === "screened" || m.analysis_stage === "selected_incomplete"; }
function fillValue(m: MarketRow): string { return fmtEstimatedFill(m.estimated_maker_fill_rate_30s_conservative, m.estimated_maker_fill_sample_quality); }
function MarketDetails({ market: m }: { market: MarketRow }) {
  const metrics = [
    { label: "取引回数 / 分", value: fmt(m.trades_per_minute_median, 1) },
    { label: "総合スコア", value: fmt(m.effective_score ?? m.score, 1), title: TOOLTIPS.effectiveScore },
    { label: "推定約定率 · $50 / 30秒", value: fillValue(m), title: ESTIMATED_FILL_TOOLTIP },
    { label: "約定5秒後", value: <SignedValue value={m.maker_markout_5s_median_bps} />, title: TOOLTIPS.makerMarkout },
    { label: "約定30秒後", value: <SignedValue value={m.maker_markout_30s_median_bps} />, title: TOOLTIPS.makerMarkout },
    { label: "総取引回数", value: m.total_trade_count == null ? "—" : m.total_trade_count.toLocaleString("ja-JP"), title: "分析対象期間内に観測した取引回数です。" },
    { label: "資金調達率（推定）", value: m.current_funding_rate == null ? "—" : `${fmt(m.current_funding_rate, 4, true)}%`, title: "観測時点の次回資金調達率の推定値です。" },
    { label: "データ網羅率", value: m.data_coverage_pct == null ? "—" : `${fmt(m.data_coverage_pct, 1)}%`, title: TOOLTIPS.coverage },
    { label: "データ品質", value: fmtSampleQuality(m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality), title: TOOLTIPS.sampleQuality },
  ];
  return <div className={styles.details}>
    <div className={styles.detailsHeading}><strong>{m.symbol}</strong><span className="muted">{m.analysis_stage === "selected_incomplete" ? "分析未完了" : m.analysis_stage === "screened" ? "簡易分析" : "詳細分析"}</span>{!isScreened(m) && <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>詳細ページ →</Link>}</div>
    <dl className={styles.metrics}>{metrics.map((metric) => <div key={metric.label} title={metric.title}><dt>{metric.label}</dt><dd className="tabular">{metric.value}</dd></div>)}</dl>
  </div>;
}

export default function HomeMarkets({ markets }: { markets: MarketRow[] }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("opportunity");
  const [candidatesOnly, setCandidatesOnly] = useState(false);
  const [liquidity, setLiquidity] = useState(true);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [expanded, setExpanded] = useState<string | null>(null);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return markets.filter((m) => (!candidatesOnly || m.is_candidate) && m.symbol.toLowerCase().includes(q)).sort((a, b) => {
      const av = sortValue(a, sort), bv = sortValue(b, sort);
      return av === bv ? ((b.effective_score ?? b.score ?? -Infinity) - (a.effective_score ?? a.score ?? -Infinity)) || a.symbol.localeCompare(b.symbol) : bv - av;
    });
  }, [markets, query, sort, candidatesOnly]);
  const visible = rows.slice(0, limit);
  return <section className={styles.section} aria-label="銘柄の比較">
    <div className={styles.controls}>
      <input type="search" placeholder="銘柄を検索" aria-label="銘柄を検索" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE_SIZE); setExpanded(null); }} />
      <select aria-label="並べ替え" title={sort === "opportunity" ? HOME_RANKING_TOOLTIP : undefined} value={sort} onChange={(e) => { const next = e.target.value as Sort; setSort(next); setLimit(PAGE_SIZE); setExpanded(null); if (next !== "score") setLiquidity(next !== "fill" && next !== "markout"); }}>{Object.entries(SORTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <label className={styles.filter}><input type="checkbox" checked={candidatesOnly} onChange={(e) => { setCandidatesOnly(e.target.checked); setLimit(PAGE_SIZE); setExpanded(null); }} />候補のみ</label>
      <div className={styles.toggle} role="group" aria-label="比較する指標"><button type="button" aria-pressed={liquidity} onClick={() => { setLiquidity(true); setSort("opportunity"); }}>流動性</button><button type="button" aria-pressed={!liquidity} onClick={() => { setLiquidity(false); if (sort === "spread" || sort === "depth" || sort === "activity") setSort("opportunity"); }}>約定・価格変化</button></div>
    </div>
    <div className={styles.resultCount}><span role="status">{visible.length} / {rows.length} 銘柄</span><span>＋で詳細</span></div>
    {rows.length === 0 ? <p className={styles.empty}>{markets.length === 0 ? "最新の分析に表示できる銘柄はありません。" : "条件に一致する銘柄はありません。"}</p> : <table className={styles.table}>
      <caption className="sr-only">{liquidity ? "銘柄の流動性比較" : "銘柄の約定・価格変化の比較"}</caption>
      <thead><tr><th scope="col">銘柄</th>
        {liquidity ? <><th scope="col" className={styles.primaryHeader} title={TOOLTIPS.tradesPerMin}>取引回数<span>/ 分 · 中央値</span></th><th scope="col" className={styles.primaryHeader} title={TOOLTIPS.depth10bp}>板の厚さ<span>±10bp · USD</span></th><th scope="col">スプレッド<span>bp</span></th><th scope="col" className={styles.desktopOnly} title={ESTIMATED_FILL_TOOLTIP}>推定約定率<span>$50 · 30秒</span></th><th scope="col" className={styles.desktopOnly} title={TOOLTIPS.effectiveScore}>総合スコア</th></> : <><th scope="col" title={TOOLTIPS.effectiveScore}>総合スコア</th><th scope="col" title={ESTIMATED_FILL_TOOLTIP}>推定約定率<span>$50 · 30秒</span></th><th scope="col" title={TOOLTIPS.makerMarkout}>約定30秒後<span>bp</span></th><th scope="col" className={styles.desktopOnly} title={TOOLTIPS.makerMarkout}>約定5秒後<span>bp</span></th><th scope="col" className={styles.desktopOnly} title={ESTIMATED_EDGE_TOOLTIP}>推定エッジ<span>30秒 · bp</span></th></>}
      </tr></thead>
      <tbody>{visible.map((m, index) => {
        const open = expanded === m.symbol, detailId = `market-detail-${m.market_id}`;
        return <Fragment key={m.symbol}>
          <tr className={`${styles.dataRow} ${index % 2 === 1 ? styles.alternate : ""} ${open ? styles.selected : ""}`}>
            <th scope="row"><div className={styles.identity}><button className={styles.expand} type="button" aria-label={`${m.symbol}の詳細${open ? "を閉じる" : "を表示"}`} aria-expanded={open} aria-controls={open ? detailId : undefined} onClick={() => setExpanded(open ? null : m.symbol)}>{open ? "−" : "+"}</button><div className={styles.symbol}>{isScreened(m) ? <span title={m.symbol}>{m.symbol}</span> : <Link title={m.symbol} href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>}{(m.is_candidate || isScreened(m)) && <small>{isScreened(m) ? (m.analysis_stage === "selected_incomplete" ? "分析未完了" : "簡易分析") : "候補"}</small>}</div></div></th>
            {liquidity ? <><td className={styles.primaryMetric}>{fmt(m.trades_per_minute_median, 1)}</td><td className={styles.primaryMetric}>{formatDepth(m.median_two_sided_depth_10bps_usd)}</td><td>{fmt(m.median_spread_bps)}</td><td className={styles.desktopOnly}>{fillValue(m)}</td><td className={`${styles.desktopOnly} ${styles.secondaryMetric}`}>{fmt(m.effective_score ?? m.score, 1)}</td></> : <><td className={styles.score}>{fmt(m.effective_score ?? m.score, 1)}</td><td>{fillValue(m)}</td><td><SignedValue value={m.maker_markout_30s_median_bps} /></td><td className={styles.desktopOnly}><SignedValue value={m.maker_markout_5s_median_bps} /></td><td className={styles.desktopOnly}><SignedValue value={m.estimated_maker_edge_30s_bps} />{m.estimated_maker_edge_fee_included === false && <small className={styles.feeNote}>手数料別</small>}</td></>}
          </tr>
          {open && <tr className={styles.detailRow}><td colSpan={6} id={detailId}><MarketDetails market={m} /></td></tr>}
        </Fragment>;
      })}</tbody>
    </table>}
    {rows.length > visible.length && <div className={styles.more}><button type="button" onClick={() => setLimit((n) => n + PAGE_SIZE)}>さらに{Math.min(PAGE_SIZE, rows.length - visible.length)}銘柄を表示</button></div>}
  </section>;
}
