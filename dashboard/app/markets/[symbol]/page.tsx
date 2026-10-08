import EstimatedFillValue from "@/components/EstimatedFillValue";
import MetricCard from "@/components/MetricCard";
import PublicErrorState from "@/components/PublicErrorState";
import QualityChip from "@/components/QualityChip";
import RankBadge from "@/components/RankBadge";
import ScoreBar from "@/components/ScoreBar";
import SignedValue from "@/components/SignedValue";
import { getMarket } from "@/lib/api";
import { fmt, fmtEstimatedFill, fmtPaperBp, fmtPaperCount, fmtPaperUsd, fmtPctFraction } from "@/lib/format";
import {
  ESTIMATED_EDGE_TOOLTIP,
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import {
  formatActivity,
  formatDepth,
  publicAssessmentCopy,
  publicDataUnavailableMessage,
  rankSubtext,
  TOOLTIPS,
} from "@/lib/public";
import Link from "next/link";

function sizeCell(
  bySize:
    | Record<
        string,
        Record<string, { conservative?: number | null; optimistic?: number | null }>
      >
    | null
    | undefined,
  size: string,
  horizon: string,
  mode: "conservative" | "optimistic",
  quality?: string | null,
): string {
  if (quality === "insufficient") return "—";
  const rate = bySize?.[size]?.[horizon]?.[mode];
  return fmtEstimatedFill(rate, quality);
}

export default async function MarketDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const m = await getMarket(symbol);
  if (!m) {
    const msg = publicDataUnavailableMessage("market");
    return (
      <section className="panel">
        <p className="back-link"><Link href="/markets">← 市場一覧</Link></p>
        <PublicErrorState title={msg.title} body={msg.body} />
      </section>
    );
  }

  const bySize = m.estimated_maker_fill_by_size;
  const fillQ = m.estimated_maker_fill_sample_quality;
  const feeLabel =
    m.estimated_maker_edge_fee_included === false
      ? "手数料別"
      : m.estimated_maker_edge_fee_included === true
        ? "手数料込"
        : null;

  return (
    <section className="panel">
      <p className="back-link">
        <Link href="/markets">← 市場一覧</Link>
      </p>

      <header className="detail-header">
        <div>
          <h1>
            {m.symbol}{" "}
            <RankBadge letter={m.letter_rank} showLabel />
          </h1>
          <p className="detail-sub">
            評価 {m.letter_rank} · スコア {fmt(m.score, 1)}
            {m.effective_score != null && m.effective_score !== m.score
              ? ` · 総合スコア ${fmt(m.effective_score, 1)}`
              : ""}
          </p>
          <p className="detail-sub">{rankSubtext(m.letter_rank, m.is_candidate)}</p>
          <p className="detail-sub muted" title={TOOLTIPS.confidence}>
            評価ランクはスコア、総合スコアはデータ信頼度を反映した市場順位に使います。
          </p>
        </div>
      </header>

      <div className="metric-grid" style={{ marginBottom: "1rem" }}>
        <MetricCard
          label="スコア"
          value={<ScoreBar score={m.score} />}
          title={TOOLTIPS.score}
        />
        <MetricCard
          label="データ信頼度"
          value={fmtPctFraction(m.confidence, 0)}
          title={TOOLTIPS.confidence}
        />
        <MetricCard
          label="総合スコア"
          value={fmt(m.effective_score ?? m.score, 1)}
          title={TOOLTIPS.effectiveScore}
        />
        <MetricCard
          label="約定シミュレーション（30秒）"
          value={
            <EstimatedFillValue
              rate={m.estimated_maker_fill_rate_30s_conservative}
              quality={fillQ}
            />
          }
          title={ESTIMATED_FILL_TOOLTIP}
        />
        <MetricCard
          label="スプレッド"
          value={
            <>
              {fmt(m.median_spread_bps)}
              <span className="unit"> bp</span>
            </>
          }
        />
        <MetricCard
          label="約定後の価格変化（5秒）"
          value={<SignedValue value={m.maker_markout_5s_median_bps} />}
          title={TOOLTIPS.makerMarkout}
        />
        <MetricCard
          label="約定後の価格変化（30秒）"
          value={<SignedValue value={m.maker_markout_30s_median_bps} />}
          title={TOOLTIPS.makerMarkout}
        />
        <MetricCard
          label="板の厚さ（±10bp）"
          value={formatDepth(m.median_two_sided_depth_10bps_usd)}
          title={TOOLTIPS.depth10bp}
        />
      </div>

      <section className="detail-section" aria-labelledby="opportunity-heading">
        <h2 id="opportunity-heading">市場の指標</h2>
        <div className="metric-grid">
          <MetricCard label="スコア" value={fmt(m.score, 1)} title={TOOLTIPS.score} />
          <MetricCard
            label="評価"
            value={<RankBadge letter={m.letter_rank} showLabel />}
          />
          <MetricCard
            label="スプレッド"
            value={
              <>
                {fmt(m.median_spread_bps)}
                <span className="unit"> bp</span>
              </>
            }
          />
          <MetricCard
            label="約定シミュレーション"
            value={
              <EstimatedFillValue
                rate={m.estimated_maker_fill_rate_30s_conservative}
                quality={fillQ}
                compact
              />
            }
            title={ESTIMATED_FILL_TOOLTIP}
          />
          <MetricCard
            label="推定エッジ"
            value={
              <>
                <SignedValue value={m.estimated_maker_edge_30s_bps} />
                {feeLabel && <span className="edge-meta">{feeLabel}</span>}
              </>
            }
            title={ESTIMATED_EDGE_TOOLTIP}
          />
        </div>
      </section>

      {m.confidence_breakdown && (
        <section className="detail-section" aria-labelledby="confidence-heading">
          <h2 id="confidence-heading">データ信頼度</h2>
          <p className="section-lead" title={TOOLTIPS.confidence}>
            サンプル数、データ網羅率、観測時間から算出した目安です。統計的な信頼区間ではありません。
          </p>
          {m.confidence_reasons && m.confidence_reasons.length > 0 && (
            <p className="muted">
              算出理由: {m.confidence_reasons.join("、")}
            </p>
          )}
          <div className="table-scroll" style={{ marginTop: "0.75rem" }}>
            <table className="market-table" style={{ minWidth: 360 }}>
              <thead>
                <tr>
                  <th>項目</th>
                  <th>信頼度</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(m.confidence_breakdown).map(([key, val]) => (
                  <tr key={key}>
                    <td>{key}</td>
                    <td className="tabular">
                      {val === null || val === undefined
                        ? "—"
                        : fmtPctFraction(val as number, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="detail-section" aria-labelledby="execution-heading">
        <h2 id="execution-heading" title={ESTIMATED_FILL_TOOLTIP}>
          約定シミュレーション
        </h2>
        <p className="section-lead">
          注文サイズごとの推定値です。順位には<strong>50ドル</strong>・30秒・保守的条件を使います。楽観値は注文が列の先頭に近い場合、保守値は表示中の最良気配数量がすべて先行する場合を想定します。実際の約定確率ではありません。
        </p>
        <div className="table-scroll" style={{ marginTop: "1rem" }}>
          <table className="market-table" style={{ minWidth: 520 }}>
            <thead>
              <tr>
                <th className="text-left">注文サイズ</th>
                <th>5秒・保守</th>
                <th>5秒・楽観</th>
                <th>30秒・保守</th>
                <th>30秒・楽観</th>
              </tr>
            </thead>
            <tbody>
              {["25", "50", "100"].map((size) => (
                <tr key={size}>
                  <td className="text-left tabular">
                    ${size}
                    {size === "50" ? "（順位基準）" : ""}
                  </td>
                  <td className="tabular">
                    {sizeCell(bySize, size, "5s", "conservative", fillQ)}
                  </td>
                  <td className="tabular">
                    {sizeCell(bySize, size, "5s", "optimistic", fillQ)}
                  </td>
                  <td className="tabular">
                    {sizeCell(bySize, size, "30s", "conservative", fillQ)}
                  </td>
                  <td className="tabular">
                    {sizeCell(bySize, size, "30s", "optimistic", fillQ)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!bySize && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            この分析データにはサイズ別の値がありません。取得できている場合、50ドルの値は上部に表示されます。
          </p>
        )}
      </section>

      <section className="detail-section" aria-labelledby="paper-mm-heading">
        <h2 id="paper-mm-heading">マーケットメイク仮想シミュレーション</h2>
        <p className="section-lead">
          公開板・取引データを使った過去データ上のシミュレーションです。実際の注文は行いません。
        </p>
        <p className="section-lead muted">
          実注文ではなく、取得済みデータ上でBest Bid / Askに仮想注文を置いた場合のシミュレーションです。
          5秒間隔の板サンプルを使用するため、注文の順番待ちや実際の約定を完全には再現しません。
        </p>
        {m.paper_mm_status && m.paper_mm_status !== "ok" ? (
          <p className="muted">
            仮想シミュレーション: {m.paper_mm_status === "not_simulated" ? "この市場は未実施" : m.paper_mm_status}
          </p>
        ) : (
          <div className="metric-grid" style={{ marginTop: "1rem" }}>
            <MetricCard
              label="仮想損益"
              value={fmtPaperUsd(m.paper_mm_total_pnl_usd, m.paper_mm_status, true)}
            />
            <MetricCard
              label="仮想損益/時"
              value={fmtPaperUsd(m.paper_mm_pnl_per_hour_usd, m.paper_mm_status, true)}
            />
            <MetricCard
              label="往復約定数"
              value={fmtPaperCount(m.paper_mm_round_trips, m.paper_mm_status)}
            />
            <MetricCard
              label="約定額"
              value={fmtPaperUsd(m.paper_mm_filled_notional_usd, m.paper_mm_status)}
            />
            <MetricCard
              label="最大保有額"
              value={fmtPaperUsd(m.paper_mm_max_abs_inventory_usd, m.paper_mm_status)}
            />
            <MetricCard
              label="保有時間の割合"
              value={
                m.paper_mm_status === "ok" && m.paper_mm_time_with_inventory_pct != null
                  ? `${fmt(m.paper_mm_time_with_inventory_pct, 1)}%`
                  : "—"
              }
            />
            <MetricCard
              label="保有時間の中央値"
              value={
                m.paper_mm_status === "ok" && m.paper_mm_median_holding_seconds != null
                  ? `${fmt(m.paper_mm_median_holding_seconds, 0)}s`
                  : "—"
              }
            />
            <MetricCard
              label="仮想約定後の価格変化（30秒）"
              value={fmtPaperBp(m.paper_mm_markout_30s_median_bps, m.paper_mm_status)}
            />
          </div>
        )}
      </section>

      <section className="detail-section" aria-labelledby="liquidity-heading">
          <h2 id="liquidity-heading">流動性・取引状況</h2>
        <div className="metric-grid">
          <MetricCard
            label="板の厚さ（±10bp）"
            value={formatDepth(m.median_two_sided_depth_10bps_usd)}
            title={TOOLTIPS.depth10bp}
          />
          <MetricCard
            label="取引回数/分"
            value={formatActivity(m.trades_per_minute_median)}
            title={TOOLTIPS.tradesPerMin}
          />
          <MetricCard
            label="総取引回数"
            value={fmt(m.total_trade_count, 0)}
            title="市場全体の取引成立数です。約定シミュレーション値とは異なります。"
          />
          <MetricCard
            label="5bp以上のスプレッド割合"
            value={
              m.pct_time_spread_ge_5bps != null
                ? `${(m.pct_time_spread_ge_5bps * 100).toFixed(0)}% ≥5bp`
                : "—"
            }
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="adverse-heading">
          <h2 id="adverse-heading">約定後の価格変化</h2>
        <div className="metric-grid">
          <MetricCard
            label="5秒後"
            value={<SignedValue value={m.maker_markout_5s_median_bps} />}
            title={TOOLTIPS.makerMarkout}
          />
          <MetricCard
            label="30秒後"
            value={<SignedValue value={m.maker_markout_30s_median_bps} />}
            title={TOOLTIPS.makerMarkout}
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="quality-heading">
        <h2 id="quality-heading">データ品質</h2>
        <div className="metric-grid">
          <MetricCard
            label="データ網羅率"
            value={
              m.data_coverage_pct != null ? `${fmt(m.data_coverage_pct, 1)}%` : "—"
            }
            title={TOOLTIPS.coverage}
          />
          <MetricCard
            label="約定推定のサンプル品質"
            value={<QualityChip quality={fillQ} />}
            title={TOOLTIPS.sampleQuality}
          />
          <MetricCard
            label="価格変化のサンプル品質"
            value={<QualityChip quality={m.markout_sample_quality} />}
            title={TOOLTIPS.sampleQuality}
          />
          <MetricCard
            label="観測時間"
            value={
              m.analysis_window_hours != null
                ? `${fmt(m.analysis_window_hours, 1)}h`
                : "—"
            }
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="assessment-heading">
        <h2 id="assessment-heading">分析メモ</h2>
        <div className="assessment-grid">
          <div className="assessment-panel assessment-strengths">
            <h3>強み</h3>
            <ul>
              {(m.pros || []).length ? (
                (m.pros || []).map((p) => <li key={p}>{publicAssessmentCopy(p)}</li>)
              ) : (
                <li>特になし</li>
              )}
            </ul>
          </div>
          <div className="assessment-panel assessment-risks">
            <h3>リスク</h3>
            <ul>
              {(m.cons || []).length ? (
                (m.cons || []).map((p) => <li key={p}>{publicAssessmentCopy(p)}</li>)
              ) : (
                <li>特になし</li>
              )}
            </ul>
          </div>
          <div className="assessment-panel assessment-notes">
            <h3>データに関する注意</h3>
            <ul>
              {(m.warnings || []).length ? (
                (m.warnings || []).map((p) => <li key={p}>{publicAssessmentCopy(p)}</li>)
              ) : (
                <li>特になし</li>
              )}
            </ul>
          </div>
        </div>
      </section>
    </section>
  );
}
