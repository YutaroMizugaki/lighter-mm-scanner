import Link from "next/link";
import type { MarketRow } from "@/lib/types";
import { fmt } from "@/lib/format";
import { TOOLTIPS } from "@/lib/public";
import EstimatedFillValue from "./EstimatedFillValue";
import QualityChip from "./QualityChip";
import ScoreBar from "./ScoreBar";

type Props = { candidates: MarketRow[]; fetchFailed?: boolean };

export default function TopOpportunities({ candidates, fetchFailed = false }: Props) {
  const top = candidates.slice(0, 5);

  return (
    <section className="panel" aria-labelledby="top-opps-heading">
      <div className="section-header section-header-row">
        <div>
          <h2 id="top-opps-heading">注目の候補</h2>
          <p className="section-lead">データの信頼度を加味したスコア順の上位5市場。</p>
        </div>
        <Link className="text-link" href="/candidates">候補をすべて見る <span aria-hidden="true">→</span></Link>
      </div>
      {fetchFailed ? (
        <p className="muted">市場データを読み込めませんでした。</p>
      ) : top.length === 0 ? (
        <p className="muted">現在、条件を満たす候補はありません。<Link href="/markets">市場一覧を見る →</Link></p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="market-table top-opps-table">
              <thead>
                <tr>
                  <th scope="col" className="sticky-col">銘柄</th>
                  <th scope="col" title={TOOLTIPS.effectiveScore}>総合スコア</th>
                  <th scope="col" title={TOOLTIPS.estimatedFill}>約定の推定</th>
                  <th scope="col">スプレッド</th>
                  <th scope="col" title={TOOLTIPS.sampleQuality}>データ品質</th>
                </tr>
              </thead>
              <tbody>
                {top.map((m, index) => (
                  <tr key={m.symbol}>
                    <td className="sticky-col">
                      <span className="row-number" aria-hidden="true">{index + 1}</span>
                      <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
                    </td>
                    <td title={TOOLTIPS.effectiveScore}><ScoreBar score={m.effective_score ?? m.score} title={TOOLTIPS.effectiveScore} /></td>
                    <td><EstimatedFillValue rate={m.estimated_maker_fill_rate_30s_conservative} quality={m.estimated_maker_fill_sample_quality} compact /></td>
                    <td className="tabular">{fmt(m.median_spread_bps)}<span className="unit"> bp</span></td>
                    <td><QualityChip quality={m.estimated_maker_fill_sample_quality ?? m.markout_sample_quality} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="table-note">約定の推定は、指値50ドル・30秒・保守的条件でのシミュレーションです。実際の約定確率ではありません。</p>
        </>
      )}
    </section>
  );
}
