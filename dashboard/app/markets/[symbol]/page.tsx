import EstimatedFillValue from "@/components/EstimatedFillValue";
import MetricCard from "@/components/MetricCard";
import PublicErrorState from "@/components/PublicErrorState";
import QualityChip from "@/components/QualityChip";
import RankBadge from "@/components/RankBadge";
import ScoreBar from "@/components/ScoreBar";
import SignedValue from "@/components/SignedValue";
import { getMarket } from "@/lib/api";
import { fmt, fmtEstimatedFill } from "@/lib/format";
import {
  ESTIMATED_EDGE_TOOLTIP,
  ESTIMATED_FILL_TOOLTIP,
} from "@/lib/marketMetrics";
import {
  formatActivity,
  formatDepth,
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
        <p className="back-link">
          <Link href="/markets">← All markets</Link>
        </p>
        <PublicErrorState title={msg.title} body={msg.body} />
      </section>
    );
  }

  const bySize = m.estimated_maker_fill_by_size;
  const fillQ = m.estimated_maker_fill_sample_quality;
  const feeLabel =
    m.estimated_maker_edge_fee_included === false
      ? "fee excl."
      : m.estimated_maker_edge_fee_included === true
        ? "fee incl."
        : null;

  return (
    <section className="panel">
      <p className="back-link">
        <Link href="/markets">← All markets</Link>
      </p>

      <header className="detail-header">
        <div>
          <h1>
            {m.symbol}{" "}
            <RankBadge letter={m.letter_rank} showLabel />
          </h1>
          <p className="detail-sub">
            Rank {m.letter_rank} · Score {fmt(m.score, 1)}
          </p>
          <p className="detail-sub">{rankSubtext(m.letter_rank, m.is_candidate)}</p>
        </div>
      </header>

      <div className="metric-grid" style={{ marginBottom: "1rem" }}>
        <MetricCard
          label="Score"
          value={<ScoreBar score={m.score} />}
          title={TOOLTIPS.score}
        />
        <MetricCard
          label="Est. Fill 30s"
          value={
            <EstimatedFillValue
              rate={m.estimated_maker_fill_rate_30s_conservative}
              quality={fillQ}
            />
          }
          title={ESTIMATED_FILL_TOOLTIP}
        />
        <MetricCard
          label="Spread"
          value={
            <>
              {fmt(m.median_spread_bps)}
              <span className="unit"> bp</span>
            </>
          }
        />
        <MetricCard
          label="Maker Markout 5s"
          value={<SignedValue value={m.maker_markout_5s_median_bps} />}
          title={TOOLTIPS.makerMarkout}
        />
        <MetricCard
          label="Maker Markout 30s"
          value={<SignedValue value={m.maker_markout_30s_median_bps} />}
          title={TOOLTIPS.makerMarkout}
        />
        <MetricCard
          label="Depth ±10bp"
          value={formatDepth(m.median_two_sided_depth_10bps_usd)}
          title={TOOLTIPS.depth10bp}
        />
      </div>

      <section className="detail-section" aria-labelledby="opportunity-heading">
        <h2 id="opportunity-heading">Opportunity</h2>
        <div className="metric-grid">
          <MetricCard label="Score" value={fmt(m.score, 1)} title={TOOLTIPS.score} />
          <MetricCard
            label="Rank"
            value={<RankBadge letter={m.letter_rank} showLabel />}
          />
          <MetricCard
            label="Spread"
            value={
              <>
                {fmt(m.median_spread_bps)}
                <span className="unit"> bp</span>
              </>
            }
          />
          <MetricCard
            label="Estimated Fill"
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
            label="Estimated Edge"
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

      <section className="detail-section" aria-labelledby="execution-heading">
        <h2 id="execution-heading" title={ESTIMATED_FILL_TOOLTIP}>
          Execution likelihood
        </h2>
        <p className="section-lead">
          Estimated Maker Fill by size. Ranking default is <strong>$50</strong> conservative.
          Optimistic assumes near front of queue; conservative assumes the full displayed touch
          size is ahead. Not actual fill probability.
        </p>
        <div className="table-scroll" style={{ marginTop: "1rem" }}>
          <table className="market-table" style={{ minWidth: 520 }}>
            <thead>
              <tr>
                <th className="text-left">Size</th>
                <th>5s cons.</th>
                <th>5s opt.</th>
                <th>30s cons.</th>
                <th>30s opt.</th>
              </tr>
            </thead>
            <tbody>
              {["25", "50", "100"].map((size) => (
                <tr key={size}>
                  <td className="text-left tabular">
                    ${size}
                    {size === "50" ? " (ranking)" : ""}
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
            Size ladder unavailable in this analysis snapshot. Top-level $50 fields still show
            above when present.
          </p>
        )}
      </section>

      <section className="detail-section" aria-labelledby="liquidity-heading">
        <h2 id="liquidity-heading">Liquidity &amp; activity</h2>
        <div className="metric-grid">
          <MetricCard
            label="Depth ±10bp"
            value={formatDepth(m.median_two_sided_depth_10bps_usd)}
            title={TOOLTIPS.depth10bp}
          />
          <MetricCard
            label="Trades/min"
            value={formatActivity(m.trades_per_minute_median)}
            title={TOOLTIPS.tradesPerMin}
          />
          <MetricCard
            label="Total trades"
            value={fmt(m.total_trade_count, 0)}
            title="Market-level trade prints (not Estimated Maker Fill)"
          />
          <MetricCard
            label="Spread persistence"
            value={
              m.pct_time_spread_ge_5bps != null
                ? `${(m.pct_time_spread_ge_5bps * 100).toFixed(0)}% ≥5bp`
                : "—"
            }
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="adverse-heading">
        <h2 id="adverse-heading">Adverse selection</h2>
        <div className="metric-grid">
          <MetricCard
            label="Maker Markout 5s"
            value={<SignedValue value={m.maker_markout_5s_median_bps} />}
            title={TOOLTIPS.makerMarkout}
          />
          <MetricCard
            label="Maker Markout 30s"
            value={<SignedValue value={m.maker_markout_30s_median_bps} />}
            title={TOOLTIPS.makerMarkout}
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="quality-heading">
        <h2 id="quality-heading">Data quality</h2>
        <div className="metric-grid">
          <MetricCard
            label="Coverage"
            value={
              m.data_coverage_pct != null ? `${fmt(m.data_coverage_pct, 1)}%` : "—"
            }
            title={TOOLTIPS.coverage}
          />
          <MetricCard
            label="Fill sample quality"
            value={<QualityChip quality={fillQ} />}
            title={TOOLTIPS.sampleQuality}
          />
          <MetricCard
            label="Markout sample quality"
            value={<QualityChip quality={m.markout_sample_quality} />}
            title={TOOLTIPS.sampleQuality}
          />
          <MetricCard
            label="Observation window"
            value={
              m.analysis_window_hours != null
                ? `${fmt(m.analysis_window_hours, 1)}h`
                : "—"
            }
          />
        </div>
      </section>

      <section className="detail-section" aria-labelledby="assessment-heading">
        <h2 id="assessment-heading">Assessment</h2>
        <div className="assessment-grid">
          <div className="assessment-panel assessment-strengths">
            <h3>Strengths</h3>
            <ul>
              {(m.pros || []).length ? (
                (m.pros || []).map((p) => <li key={p}>{p}</li>)
              ) : (
                <li>none</li>
              )}
            </ul>
          </div>
          <div className="assessment-panel assessment-risks">
            <h3>Risks</h3>
            <ul>
              {(m.cons || []).length ? (
                (m.cons || []).map((p) => <li key={p}>{p}</li>)
              ) : (
                <li>none</li>
              )}
            </ul>
          </div>
          <div className="assessment-panel assessment-notes">
            <h3>Data notes</h3>
            <ul>
              {(m.warnings || []).length ? (
                (m.warnings || []).map((p) => <li key={p}>{p}</li>)
              ) : (
                <li>none</li>
              )}
            </ul>
          </div>
        </div>
      </section>
    </section>
  );
}
