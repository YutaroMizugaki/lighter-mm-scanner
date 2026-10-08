import EstimatedFillValue from "@/components/EstimatedFillValue";
import PublicErrorState from "@/components/PublicErrorState";
import QualityChip from "@/components/QualityChip";
import SignedValue from "@/components/SignedValue";
import { getCandidatesResult } from "@/lib/api";
import { fmt } from "@/lib/format";
import {
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import { publicDataUnavailableMessage, TOOLTIPS } from "@/lib/public";
import Link from "next/link";

export default async function CandidatesPage() {
  const result = await getCandidatesResult();

  if (!result.ok) {
    const msg = publicDataUnavailableMessage("markets");
    return <PublicErrorState title={msg.title} body={msg.body} />;
  }

  const candidates = result.data.candidates ?? [];

  return (
    <section className="panel">
      <div className="section-header">
        <h1 style={{ margin: 0, fontSize: "1.35rem" }}>候補市場</h1>
        <p className="section-lead" title={ESTIMATED_FILL_TOOLTIP}>
          現在の候補条件を満たす市場です。約定シミュレーションは「指値50ドル・30秒・保守的条件」の推定値で、市場全体の取引回数とは異なります。
        </p>
      </div>
      {candidates.length === 0 ? (
        <>
          <p>現在、すべての候補条件を満たす市場はありません。</p>
          <p>
            <Link href="/markets">市場一覧を見る →</Link>
          </p>
        </>
      ) : (
        <div className="table-scroll">
          <table className="market-table candidate-table">
            <thead>
              <tr>
                <th className="sticky-col">銘柄</th>
                <th title={TOOLTIPS.effectiveScore}>総合スコア</th>
                <th title={ESTIMATED_FILL_TOOLTIP}>約定シミュレーション</th>
                <th>スプレッド</th>
                <th title={TOOLTIPS.makerMarkout}>30秒後</th>
                <th title={TOOLTIPS.sampleQuality}>データ品質</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((m) => (
                <tr key={m.symbol}>
                  <td className="sticky-col">
                    <Link href={`/markets/${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
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
                  <td>
                    <SignedValue value={m.maker_markout_30s_median_bps} />
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
