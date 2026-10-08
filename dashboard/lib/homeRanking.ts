import type { MarketRow } from "./types";

// Display priorities use the existing scorer's positive liquidity benchmarks.
// Once liquidity is adequate, preserve its spread/fill/markout assessment.
const ACTIVE_TRADES_PER_MINUTE = 5;
const SUFFICIENT_DEPTH_USD = 1_000;

export const HOME_RANKING_TOOLTIP =
  "総合スコアに流動性の補正を掛けた表示順です。取引回数は中央値5回/分、板の厚さは±10bp内の両側合計$1,000を基準に、不足する割合だけ順位を下げます。基準以上では流動性だけで加点しません。分析スコア自体は変更しません。";

function liquidityFactor(value: number | null, benchmark: number): number {
  return value != null && Number.isFinite(value) ? Math.min(1, Math.max(0, value) / benchmark) : 0;
}

export function homeOpportunityPriority(m: MarketRow): number {
  const score = m.effective_score ?? m.score;
  if (score == null || !Number.isFinite(score)) return -Infinity;
  return Math.max(0, score)
    * liquidityFactor(m.trades_per_minute_median, ACTIVE_TRADES_PER_MINUTE)
    * liquidityFactor(m.median_two_sided_depth_10bps_usd, SUFFICIENT_DEPTH_USD);
}
